"""Presupuesto de tokens y reparto de tareas entre IA (core/usage.py, ChainAI, caché y prompts cortos)."""
from __future__ import annotations

import json
import time
from collections import deque

import httpx
import pytest

from core import candidates, interview, settings, usage
from core.ai import AIError, ChainAI, GeminiAI, OpenAICompatAI, set_ai


@pytest.fixture(autouse=True)
def _reset_ai():
    yield
    set_ai(None)


def _listing(i, title, price=1000, store="a"):
    return {"store": store, "store_name": store.upper(), "title": title, "price": price, "list": None,
            "cuotas": None, "promo": None, "url": f"https://{store}/{i}"}


PROFILE = interview.normalize_profile({"product": "Notebook", "category": "informatica", "must": ["32 GB de RAM"],
                                       "search_queries": ["notebook"]})


def _ids(messages):
    return [int(line.split(" | ")[0]) for line in messages[0]["content"].split("PUBLICACIONES\n")[1].splitlines()]


# ------------------------------------------------------------------ registro de uso y cuotas
def test_limites_por_modelo_vienen_de_la_configuracion():
    assert usage.limits_for("gemini", "gemini-3.1-flash-lite")["rpd"] == 500
    assert usage.limits_for("gemini", "gemini-3-flash-preview")["rpd"] == 20
    assert usage.limits_for("gemini", "gemini-flash-lite-latest")["rpm"] == 15
    assert usage.limits_for("groq", "openai/gpt-oss-120b")["tpm"] == 8000
    assert usage.limits_for("inventada", "x") == {}


def test_la_cuota_se_agota_por_tokens_por_minuto_y_se_recupera():
    L = usage.Ledger()
    assert L.fits("groq", "m", 3000)
    L.record("groq", "m", "interview", 3000, 2000, 200, 900)          # 5.000 tokens en el minuto
    assert L.fits("groq", "m", 1500)                                    # 5.000 + 1.500 < 6.800 (85 % de 8.000)
    assert not L.fits("groq", "m", 2500)                                # 7.500 > 6.800
    L.minute[("groq", "m")] = deque([(time.time() - 61, 5000)])         # pasó más de un minuto
    assert L.fits("groq", "m", 2500)


def test_un_429_no_gasta_cuota_y_baja_la_salud():
    L = usage.Ledger()
    for _ in range(4):
        L.record("gemini", "gemini-3.1-flash-lite", "evaluate", 0, 0, 429, 100)
    assert L.used("gemini", "gemini-3.1-flash-lite")["rpd"] == 0
    assert L.health("gemini") == 0.0
    assert L.health("groq") == 1.0                    # sin datos se la considera sana
    for _ in range(8):
        L.record("gemini", "x", "evaluate", 100, 50, 200, 100)
    assert L.health("gemini") == 1.0                  # sólo cuentan las últimas 8


def test_estimacion_de_tokens_crece_con_la_tanda_y_con_el_perfil():
    chica = usage.estimate_call("sys", [{"role": "user", "content": "PERFIL\nx\n\nPUBLICACIONES\na\nb"}], "evaluate")
    texto = "PERFIL\nx\n\nPUBLICACIONES\n" + "\n".join("a" * 80 for _ in range(30))
    grande = usage.estimate_call("sys", [{"role": "user", "content": texto}], "evaluate")
    assert grande > chica * 5
    assert usage.estimate_call("s", [{"role": "user", "content": "x"}], "profile") > \
        usage.estimate_call("s", [{"role": "user", "content": "x"}], "interview")


def test_el_resumen_agrupa_por_tarea_y_modelo():
    L = usage.Ledger()
    L.record("groq", "m", "interview", 1200, 300, 200, 1000)
    L.record("groq", "m", "interview", 1400, 500, 200, 1200)
    L.record("groq", "m", "interview", 0, 0, 429, 50)
    s = L.summary()
    assert s["by_task"] == [{"task": "interview", "provider": "groq", "model": "m", "calls": 2, "prompt_avg": 1300,
                             "output_avg": 400, "thoughts_avg": 0, "ms_avg": 1100, "tokens_total": 3400}]
    assert s["failed"] == 1


# ------------------------------------------------------------------ cuota dentro de cada IA
def test_una_ia_no_intenta_lo_que_no_le_entra():
    pedidos = []

    def handler(req):
        pedidos.append(json.loads(req.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": 1}'}}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
    g = OpenAICompatAI("groq", "https://x/v1", "k", ["grande", "chico"], httpx.Client(transport=httpx.MockTransport(handler)))
    usage.ledger.record("groq", "grande", "interview", 3500, 3000, 200, 100)       # "grande" ya gastó 6.500 de 6.800
    assert g.json("s", [{"role": "user", "content": "x" * 3000}], "interview") == {"ok": 1}
    assert pedidos == ["chico"]                       # a "grande" ni se le consultó
    usage.ledger.record("groq", "chico", "interview", 6000, 800, 200, 100)
    with pytest.raises(AIError, match="sin cuota"):
        g.json("s", [{"role": "user", "content": "x" * 3000}], "interview")
    assert pedidos == ["chico"]


def test_los_tokens_reales_se_registran():
    def handler(req):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"a": 1}'}]}}],
                                         "usageMetadata": {"promptTokenCount": 1234, "candidatesTokenCount": 56,
                                                           "thoughtsTokenCount": 7}})
    GeminiAI("k", "gemini-3.1-flash-lite", httpx.Client(transport=httpx.MockTransport(handler))).json(
        "s", [{"role": "user", "content": "x"}], "interview")
    [c] = usage.ledger.calls
    assert (c["provider"], c["task"], c["prompt"], c["output"], c["thoughts"], c["status"]) == \
        ("gemini", "interview", 1234, 56, 7, 200)


# ------------------------------------------------------------------ reparto de tareas
class Fija:
    def __init__(self, name, ok=True):
        self.name, self.ok, self.llamadas = name, ok, []

    def json(self, system, messages, task):
        self.llamadas.append(task)
        if not self.ok:
            raise AIError(f"{self.name} caída")
        return {"de": self.name}


MSG = [{"role": "user", "content": "x"}]


def test_cada_tarea_va_primero_a_su_ia():
    groq, gemini = Fija("groq"), Fija("gemini")
    chain = ChainAI([gemini, groq], settings.ai_routes())
    assert chain.json("s", MSG, "interview") == {"de": "groq"}
    assert chain.json("s", MSG, "profile") == {"de": "gemini"}
    assert chain.json("s", MSG, "evaluate") == {"de": "gemini"}
    assert groq.llamadas == ["interview"] and gemini.llamadas == ["profile", "evaluate"]


def test_si_la_preferida_cae_se_usa_la_siguiente_y_la_enferma_pasa_atras():
    groq, gemini = Fija("groq", ok=False), Fija("gemini")
    chain = ChainAI([gemini, groq], settings.ai_routes())
    assert chain.json("s", MSG, "interview") == {"de": "gemini"}
    for _ in range(4):                                      # groq viene fallando
        usage.ledger.record("groq", "m", "interview", 0, 0, 503, 10)
    assert [p.name for p in chain.order("interview")] == ["gemini", "groq"]   # ya no es la primera
    for _ in range(8):
        usage.ledger.record("groq", "m", "interview", 100, 50, 200, 10)
    assert [p.name for p in chain.order("interview")] == ["groq", "gemini"]   # se recuperó


def test_las_rutas_se_pueden_cambiar_por_entorno(monkeypatch):
    monkeypatch.setenv("AI_ROUTE_EVALUATE", "groq, gemini")
    assert settings.ai_routes()["evaluate"] == ["groq", "gemini"]
    assert settings.ai_routes()["interview"][0] == "groq"


# ------------------------------------------------------------------ ahorro de tokens
def test_el_prompt_corto_no_trae_la_parte_del_perfil_y_cuesta_la_mitad():
    corto, completo = interview.system_prompt(False), interview.system_prompt(True)
    assert "EL PERFIL" not in corto and "EL PERFIL" in completo
    assert usage.estimate_tokens(corto) < usage.estimate_tokens(completo) * 0.55
    assert '"type": "question"' in corto


def test_los_primeros_turnos_usan_el_prompt_corto_y_la_tarea_interview():
    vistos = []

    class Registra:
        name = "r"

        def json(self, system, messages, task):
            vistos.append((task, "EL PERFIL" in system))
            return {"type": "question", "question": "¿q?"}
    set_ai(Registra())
    conv = [{"role": "user", "content": "un mouse"}]
    interview.step(conv)                                   # 0 preguntas hechas: no puede cerrar
    for _ in range(settings.MIN_QUESTIONS):
        conv += [{"role": "assistant", "content": json.dumps({"type": "question", "question": "q"})},
                 {"role": "user", "content": "r"}]
    interview.step(conv)                                   # ya puede cerrar: prompt completo, tarea "profile"
    assert vistos == [("interview", False), ("profile", True)]


def test_la_evaluacion_repetida_sale_de_la_cache():
    pedidos = []

    class Ev:
        name = "ev"

        def json(self, system, messages, task):
            ids = _ids(messages)
            pedidos.append(len(ids))
            return {"items": [{"id": i, "model": f"M{i}", "score": 70} for i in ids]}
    set_ai(Ev())
    ls = [_listing(i, f"Notebook modelo {i}") for i in range(6)]
    assert len(candidates.evaluate(ls, PROFILE)) == 6 and pedidos == [6]
    otra_tienda = [_listing(i, f"Notebook modelo {i}", price=999, store="b") for i in range(6)]
    assert len(candidates.evaluate(otra_tienda, PROFILE)) == 6 and pedidos == [6]      # ni se consultó
    otro_perfil = interview.normalize_profile({"product": "Notebook", "category": "informatica",
                                               "must": ["16 GB"], "search_queries": ["notebook"]})
    candidates.evaluate(ls, otro_perfil)
    assert pedidos == [6, 6]                                                           # otra persona: se evalúa


def test_lo_que_la_ia_dejo_sin_responder_se_vuelve_a_pedir():
    pedidos = []

    class Perezosa:
        name = "p"

        def json(self, system, messages, task):
            ids = _ids(messages)
            pedidos.append(ids)
            if len(pedidos) == 1:       # primera vez: contesta sólo 2 de 12 (una como "no es el producto")
                return {"items": [{"id": 0, "model": "M0", "score": 80}, {"id": 1, "product": False}]}
            return {"items": [{"id": i, "model": f"M{i}", "score": 60} for i in ids]}
    set_ai(Perezosa())
    out = candidates.evaluate_batch([_listing(i, f"Notebook {i}") for i in range(12)], PROFILE)
    assert len(pedidos) == 2 and len(pedidos[1]) == 10                  # se pidió de nuevo sólo lo que faltó
    assert len(out) == 11                                               # 1 evaluada + 10 reintentadas; 1 "no es el producto"


def test_lo_descartado_por_no_ser_el_producto_tambien_se_recuerda():
    pedidos = []

    class Mixta:
        name = "m"

        def json(self, system, messages, task):
            ids = _ids(messages)
            pedidos.append(ids)
            return {"items": [{"id": i, "model": f"M{i}", "score": 70} if i % 2 == 0 else {"id": i, "product": False}
                              for i in ids]}
    set_ai(Mixta())
    ls = [_listing(i, f"Notebook {i}" if i % 2 == 0 else f"Funda para notebook {i}") for i in range(6)]
    assert len(candidates.evaluate(ls, PROFILE)) == 3 and pedidos == [[0, 1, 2, 3, 4, 5]]
    segunda = candidates.evaluate(ls, PROFILE)
    assert len(segunda) == 3 and all(e["score"] == 70 for e in segunda)      # los 3 que sí; ninguno vacío
    assert pedidos == [[0, 1, 2, 3, 4, 5]]                                   # nada se volvió a pedir
