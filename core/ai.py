"""Capa única para hablar con la IA.

Todo el resto del código llama a `get_ai().json(system, messages)` y recibe un dict.
Cambiar de proveedor (Gemini gratis hoy, otro pago mañana) es agregar una clase acá.

- GeminiAI: API REST de Google AI Studio, con respuesta forzada a JSON.
- El proveedor "demo" vive en core/demo.py (sin IA, para probar la interfaz sin clave).
"""
from __future__ import annotations

import json
import os
import logging
import re
import time
from typing import Protocol

import httpx

from core import settings

log = logging.getLogger("ai")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
# Cuánto esperar a un modelo antes de pasar al siguiente: una pregunta tarda 2-5 s; la evaluación
# de candidatos manda mucho texto y puede tardar más.
TIMEOUTS = {"interview": 20, "evaluate": 60}
RETRIES = 1          # por modelo; después se pasa al siguiente de la lista
RETRY_WAIT_S = 1.5


class AIError(RuntimeError):
    """Error explicable al usuario (sin clave, límite gratis agotado, modelo retirado...)."""


class AI(Protocol):
    name: str

    def json(self, system: str, messages: list[dict], task: str) -> dict:
        """messages = [{"role": "user"|"assistant", "content": str}]. `task` identifica el
        prompt ("interview", "evaluate") para los proveedores que no usan IA real."""
        ...


def parse_json_text(text: str) -> dict:
    """Los modelos a veces envuelven el JSON en ```json ... ``` o agregan texto: lo rescatamos."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        out = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise AIError("La IA respondió algo que no es JSON.") from None
        try:
            out = json.loads(text[start:end + 1])
        except ValueError:
            raise AIError("La IA respondió un JSON incompleto.") from None
    if not isinstance(out, dict):
        raise AIError("La IA respondió un JSON inesperado.")
    return out


class GeminiAI:
    name = "gemini"

    def __init__(self, key: str, model: str, client: httpx.Client | None = None):
        """`model` puede ser una lista separada por comas: se prueban en orden y, si uno está
        saturado, sin cupo gratis o retirado, se pasa al siguiente."""
        if not key:
            raise AIError("Falta GEMINI_API_KEY en el archivo .env (es gratis: "
                          "https://aistudio.google.com/apikey). O usá AI_PROVIDER=demo.")
        self.key = key
        self.models = [m.strip() for m in model.split(",") if m.strip()] or ["gemini-flash-latest"]
        self.model = self.models[0]
        self.client = client or httpx.Client(timeout=60)

    def json(self, system: str, messages: list[dict], task: str) -> dict:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "model" if m["role"] == "assistant" else "user",
                          "parts": [{"text": m["content"]}]} for m in messages],
            # La entrevista charla (0.4); la evaluación tiene que dar lo mismo cada vez (0.1)
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1 if task == "evaluate" else 0.4},
        }
        r, failures = None, []
        available = [m for m in self.models if not _cooling(m)]
        for model in available:
            gc = dict(body["generationConfig"])
            if model.startswith("gemini-3"):
                # Razonamiento corto: de ~3,5 s a ~2 s por pregunta, con la misma calidad en la entrevista
                gc["thinkingConfig"] = {"thinkingLevel": "low"}
            r = self._post(model, {**body, "generationConfig": gc}, TIMEOUTS.get(task, 30))
            if r is not None and r.status_code not in (404, 429, 500, 503):
                self.model = model
                break
            failures.append(f"{model}: {'sin respuesta' if r is None else 'HTTP %d' % r.status_code}")
            _cool_down(model, r)
            log.info("Gemini %s no disponible, pruebo el siguiente", failures[-1])
        else:
            codes = {f.rsplit(" ", 1)[-1] for f in failures}
            if failures and codes == {"404"}:
                raise AIError(f"Google ya no ofrece {', '.join(self.models)}. Cambiá GEMINI_MODEL en .env. "
                              f"Disponibles: {self._flash_models()}")
            wait = _seconds_until_free(self.models)
            when = f"en {wait} segundos" if wait < 90 else f"en {wait // 60 + 1} minutos"
            limited = "HTTP 429" in codes or (not failures and any(_cool_reason.get(m) == 429 for m in self.models))
            if limited:
                raise AIError(f"Llegamos al límite gratis de la IA por ahora (muchas consultas juntas). "
                              f"Probá de nuevo {when}.")
            raise AIError(f"La IA de Google está saturada en este momento. Probá de nuevo {when}.")
        if r.status_code in (400, 401, 403):
            raise AIError(f"Gemini rechazó el pedido (HTTP {r.status_code}): revisá GEMINI_API_KEY. "
                          f"{_err_detail(r)}")
        if r.status_code != 200:
            raise AIError(f"Gemini respondió HTTP {r.status_code}. {_err_detail(r)}")
        data = r.json()
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError):
            raise AIError("Gemini no devolvió respuesta (puede haberla bloqueado por seguridad).") from None
        return parse_json_text(text)

    def _post(self, model: str, body: dict, timeout: float = 30) -> httpx.Response | None:
        """Un modelo, con reintentos cortos si está saturado. None si no respondió a tiempo."""
        r = None
        for attempt in range(RETRIES + 1):
            try:
                r = self.client.post(GEMINI_URL.format(model=model), json=body,
                                     headers={"x-goog-api-key": self.key}, timeout=timeout)
            except httpx.TimeoutException:
                return None
            # 500/503: Google saturado (pasa seguido en el plan gratis). Suele durar segundos.
            if r.status_code not in (500, 503) or attempt == RETRIES:
                return r
            time.sleep(RETRY_WAIT_S * (attempt + 1))
        return r

    def _flash_models(self) -> str:
        try:
            r = self.client.get(GEMINI_MODELS_URL, headers={"x-goog-api-key": self.key})
            names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
            flash = [n for n in names if "flash" in n] or names
            return ", ".join(flash[:8]) or "(no se pudo listar)"
        except Exception:  # noqa: BLE001
            return "(no se pudo listar)"


# Modelos que hay que saltear por un rato: {modelo: hasta cuándo (time.time())}. En memoria del servidor.
_cooldown: dict[str, float] = {}
_cool_reason: dict[str, int] = {}   # 429 = sin cupo, 503 = saturado
COOL_DEFAULT_S = 60      # 429 sin "retryDelay": límite por minuto
COOL_DAILY_S = 3600      # 429 por cupo diario: se vuelve a probar en una hora
COOL_BUSY_S = 30         # 503 / sin respuesta


def _cooling(model: str) -> bool:
    return _cooldown.get(model, 0) > time.time()


def _cool_down(model: str, r: httpx.Response | None) -> None:
    """Después de un 429 Google dice cuánto esperar (retryDelay) y qué cupo se agotó (quotaId)."""
    if r is not None and r.status_code == 404:
        return
    secs = COOL_BUSY_S
    if r is not None and r.status_code == 429:
        text = r.text
        delay = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', text)
        secs = float(delay.group(1)) + 1 if delay else COOL_DEFAULT_S
        if re.search(r"PerDay", text):
            secs = max(secs, COOL_DAILY_S)
    _cooldown[model] = time.time() + secs
    _cool_reason[model] = 429 if r is not None and r.status_code == 429 else 503


def _seconds_until_free(models: list[str]) -> int:
    waits = [_cooldown.get(m, 0) - time.time() for m in models]
    return max(5, int(min(waits)) + 1) if waits else 60


def _err_detail(r: httpx.Response) -> str:
    try:
        return (r.json().get("error") or {}).get("message", "")[:200]
    except ValueError:
        return r.text[:200]


_override: AI | None = None


def set_ai(ai: AI | None) -> None:
    """Para tests: fuerza un proveedor."""
    global _override
    _override = ai


# ------------------------------------------------------------------ otras IA gratis (API tipo OpenAI)
# Proveedores con plan gratis y API compatible con la de OpenAI: con una clave cada uno, la app los usa
# en cadena. (url base, variable de la clave, variable de los modelos, modelos por defecto)
OPENAI_COMPAT = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "GROQ_MODELS",
             "openai/gpt-oss-120b,openai/gpt-oss-20b"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "CEREBRAS_MODELS",
                 "gpt-oss-120b,llama-3.3-70b,qwen-3-32b"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "OPENROUTER_MODELS",
                   "nvidia/nemotron-3.5-lightning:free,nvidia/nemotron-3-ultra-550b-a55b:free,google/gemma-4-31b-it:free"),
}


class OpenAICompatAI:
    """Groq, Cerebras, OpenRouter...: /chat/completions con respuesta en JSON. Misma lógica que Gemini:
    varios modelos en orden y los que dan 429 o fallan se saltean un rato."""

    def __init__(self, name: str, base_url: str, key: str, models: list[str], client: httpx.Client | None = None):
        self.name, self.base_url, self.key = name, base_url.rstrip("/"), key
        self.models = models
        self.model = models[0] if models else ""
        self.client = client or httpx.Client(timeout=60)

    def json(self, system: str, messages: list[dict], task: str) -> dict:
        msgs = [{"role": "system", "content": system}] + [
            {"role": "assistant" if m["role"] == "assistant" else "user", "content": m["content"]} for m in messages]
        failures = []
        for model in [m for m in self.models if not _cooling(f"{self.name}:{m}")]:
            body = {"model": model, "messages": msgs, "temperature": 0.1 if task == "evaluate" else 0.4,
                    "response_format": {"type": "json_object"}}
            try:
                r = self.client.post(f"{self.base_url}/chat/completions", json=body, timeout=TIMEOUTS.get(task, 30),
                                     headers={"Authorization": f"Bearer {self.key}"})
                if r.status_code == 400 and "response_format" in r.text and "json_validate_failed" not in r.text:
                    # Modelo sin modo JSON: se pide igual y se rescata el JSON del texto
                    body.pop("response_format")
                    r = self.client.post(f"{self.base_url}/chat/completions", json=body, timeout=TIMEOUTS.get(task, 30),
                                         headers={"Authorization": f"Bearer {self.key}"})
            except httpx.HTTPError:
                r = None
            if r is not None and r.status_code == 200:
                try:
                    text = r.json()["choices"][0]["message"]["content"] or ""
                    out = parse_json_text(text)
                    self.model = model
                    return out
                except (KeyError, IndexError, ValueError, AIError):
                    failures.append(f"{model}: respuesta inválida")
                    continue
            code = None if r is None else r.status_code
            failures.append(f"{model}: {'sin respuesta' if r is None else 'HTTP %d' % code}")
            if code in (401, 403):
                raise AIError(f"{self.name} rechazó la clave (HTTP {code}).")
            secs = COOL_BUSY_S
            if code == 429:
                try:
                    secs = float(r.headers.get("retry-after") or COOL_DEFAULT_S) + 1
                except ValueError:
                    secs = COOL_DEFAULT_S
            if code == 400:
                # La respuesta no entró o no fue JSON válido (pedido demasiado largo): el modelo anda, no se pausa
                raise AIError(f"{self.name} no pudo con este pedido (HTTP 400, probablemente demasiado largo).")
            if code != 404:
                _cooldown[f"{self.name}:{model}"] = time.time() + secs
                _cool_reason[f"{self.name}:{model}"] = 429 if code == 429 else 503
        raise AIError(f"{self.name} no disponible ahora ({'; '.join(failures) or 'todos en pausa'}).")


class ChainAI:
    """Varias IA en cadena: si una está sin cupo o falla, se usa la siguiente."""

    def __init__(self, providers: list):
        self.providers = providers
        self.name = " + ".join(p.name for p in providers)
        self.model = ""

    def json(self, system: str, messages: list[dict], task: str) -> dict:
        errors = []
        for p in self.providers:
            try:
                out = p.json(system, messages, task)
                self.model = f"{p.name}:{getattr(p, 'model', '')}"
                return out
            except AIError as e:
                errors.append(e)
                log.info("%s sin respuesta, pruebo la siguiente IA: %s", p.name, e)
        if len(errors) == 1:
            raise errors[0]
        raise AIError("Todas las IA están sin cupo o saturadas en este momento. Probá de nuevo en un rato.")


def configured_providers(client: httpx.Client | None = None) -> list:
    """Las IA con clave, en el orden de AI_CHAIN (por defecto gemini, groq, openrouter)."""
    out = []
    for name in settings.ai_chain():
        if name == "gemini" and settings.gemini_key():
            out.append(GeminiAI(settings.gemini_key(), settings.gemini_model(), client))
        elif name in OPENAI_COMPAT:
            base, key_env, models_env, default = OPENAI_COMPAT[name]
            key = (os.getenv(key_env) or "").strip()
            if key:
                models = [m.strip() for m in (os.getenv(models_env) or default).split(",") if m.strip()]
                out.append(OpenAICompatAI(name, base, key, models, client))
    return out


def get_ai() -> AI:
    if _override is not None:
        return _override
    provider = settings.ai_provider()
    if provider == "demo":
        from core.demo import DemoAI
        return DemoAI()
    providers = configured_providers()
    if not providers:
        raise AIError("No hay ninguna IA configurada: poné GEMINI_API_KEY (o GROQ_API_KEY, CEREBRAS_API_KEY, "
                      "OPENROUTER_API_KEY) en .env, o usá AI_PROVIDER=demo.")
    return providers[0] if len(providers) == 1 else ChainAI(providers)
