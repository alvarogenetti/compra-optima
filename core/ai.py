"""Capa única para hablar con la IA.

Todo el resto del código llama a `get_ai().json(system, messages)` y recibe un dict.
Cambiar de proveedor (Gemini gratis hoy, otro pago mañana) es agregar una clase acá.

- GeminiAI: API REST de Google AI Studio, con respuesta forzada a JSON.
- El proveedor "demo" vive en core/demo.py (sin IA, para probar la interfaz sin clave).
"""
from __future__ import annotations

import json
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
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
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


def get_ai() -> AI:
    if _override is not None:
        return _override
    provider = settings.ai_provider()
    if provider == "demo":
        from core.demo import DemoAI
        return DemoAI()
    if provider == "gemini":
        return GeminiAI(settings.gemini_key(), settings.gemini_model())
    raise AIError(f"AI_PROVIDER desconocido: {provider!r} (usá 'gemini' o 'demo').")
