"""Tests sin red: la IA y las tiendas se reemplazan por dobles."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from core import candidates, interview, search, settings, stores
from core.ai import AIError, GeminiAI, parse_json_text, set_ai
from core.demo import DemoAI, clean_query, guess_category


class FakeAI:
    """Devuelve respuestas preparadas por tarea y guarda lo que se le pidió."""
    name = "fake"

    def __init__(self, **by_task):
        self.by_task = by_task
        self.calls = []

    def json(self, system, messages, task):
        self.calls.append((task, messages))
        r = self.by_task[task]
        return r(messages) if callable(r) else r


@pytest.fixture(autouse=True)
def _reset_ai():
    yield
    set_ai(None)


# ------------------------------------------------------------------ IA
def test_parse_json_text_tolera_bloques_y_texto():
    assert parse_json_text('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_text('Acá va: {"a": 2} listo') == {"a": 2}
    with pytest.raises(AIError):
        parse_json_text("nada")


def test_gemini_pasa_al_siguiente_modelo_si_esta_saturado(monkeypatch):
    monkeypatch.setattr("core.ai.RETRY_WAIT_S", 0)
    monkeypatch.setattr("core.ai._cooldown", {})
    pedidos = []

    def handler(req: httpx.Request):
        pedidos.append(req.url.path.split("/")[-1])
        if "saturado" in str(req.url):
            return httpx.Response(503)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]})

    ai = GeminiAI("k", "saturado, bueno", httpx.Client(transport=httpx.MockTransport(handler)))
    assert ai.json("s", [{"role": "user", "content": "x"}], "interview") == {"ok": 1}
    assert ai.model == "bueno"
    assert pedidos == ["saturado:generateContent"] * 2 + ["bueno:generateContent"]
    todos_mal = GeminiAI("k", "saturado", httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AIError, match="saturada"):
        todos_mal.json("s", [{"role": "user", "content": "x"}], "interview")


def test_gemini_arma_el_pedido_y_explica_errores():
    seen = {}

    def handler(req: httpx.Request):
        if req.method == "GET":
            return httpx.Response(200, json={"models": [
                {"name": "models/gemini-3-flash", "supportedGenerationMethods": ["generateContent"]}]})
        seen["body"] = json.loads(req.content)
        seen["key"] = req.headers["x-goog-api-key"]
        if "viejo" in str(req.url):
            return httpx.Response(404, json={"error": {"message": "not found"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ai = GeminiAI("clave", "gemini-x", client)
    assert ai.json("sys", [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "{}"}],
                   "interview") == {"ok": True}
    assert seen["key"] == "clave"
    assert [c["role"] for c in seen["body"]["contents"]] == ["user", "model"]
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    with pytest.raises(AIError, match="gemini-3-flash"):
        GeminiAI("clave", "viejo", client).json("s", [{"role": "user", "content": "x"}], "interview")
    with pytest.raises(AIError, match="GEMINI_API_KEY"):
        GeminiAI("", "m")


# ------------------------------------------------------------------ entrevista
def test_normalize_pregunta_recorta_y_limpia():
    out = interview.normalize({"type": "question", "question": "¿Para qué?", "options": ["a", "A", "b", ""] + ["x"] * 9,
                               "draft": [{"label": "Uso", "value": "juegos"}, {"label": "", "value": "x"}]})
    assert out["options"] == ["a", "b", "x"]
    assert out["draft"] == [{"label": "Uso", "value": "juegos"}]
    with pytest.raises(AIError):
        interview.normalize({"type": "question", "question": ""})


def test_normalize_perfil_categoria_y_presupuesto():
    p = interview.normalize_profile({"product": "Mouse", "category": "INFORMATICA",
                                     "must": ["Inalámbrico", {"feature": "Ergonómico", "why": "muñeca"}],
                                     "budget": {"level": "medio", "max_ars": "80000"}, "search_queries": []})
    assert p["category"] == "informatica"
    assert p["must"][0]["feature"] == "Inalámbrico" and p["must"][1]["why"] == "muñeca"
    assert p["budget"] == {"level": "medio", "max_ars": 80000.0, "note": ""}
    assert p["search_queries"] == ["mouse"]
    assert interview.normalize_profile({"category": "inventada"})["category"] in stores.load().categories


def test_step_fuerza_el_perfil_al_llegar_al_maximo():
    profile = {"type": "profile", "profile": {"product": "Mouse", "category": "informatica"}}
    fake = FakeAI(interview=lambda msgs: profile if "sistema" in msgs[-1]["content"] else
                  {"type": "question", "question": "¿Otra?"})
    set_ai(fake)
    conv = [{"role": "user", "content": "un mouse"}]
    for _ in range(settings.MAX_QUESTIONS):
        conv += [{"role": "assistant", "content": json.dumps({"type": "question", "question": "q"})},
                 {"role": "user", "content": "r"}]
    out = interview.step(conv)
    assert out["type"] == "profile"
    assert "preguntas permitidas" in fake.calls[-1][1][-1]["content"]


def test_step_pide_arrancar_con_el_usuario():
    with pytest.raises(AIError):
        interview.step([{"role": "assistant", "content": "{}"}])


def test_demo_hace_la_entrevista_completa():
    set_ai(DemoAI())
    conv = [{"role": "user", "content": "Quiero una secadora de pelo"}]
    for _ in range(10):
        out = interview.step(conv)
        if out["type"] == "profile":
            break
        conv += [{"role": "assistant", "content": json.dumps(out)}, {"role": "user", "content": out["options"][0]}]
    assert out["type"] == "profile"
    assert out["profile"]["category"] == "cuidado-personal"
    assert out["profile"]["search_queries"] == ["secadora de pelo"]


def test_demo_explica_si_no_sabe():
    set_ai(DemoAI())
    first = interview.step([{"role": "user", "content": "un mouse"}])
    again = interview.step([{"role": "user", "content": "un mouse"},
                            {"role": "assistant", "content": json.dumps(first)},
                            {"role": "user", "content": "No sé, explicame las opciones"}])
    assert again["explanation"] and again["question"] == first["question"]


def test_helpers_demo():
    assert clean_query("Quiero comprarme un mouse para jugar") == "mouse jugar"
    assert guess_category("una freidora de aire") == "pequenos-electro"


# ------------------------------------------------------------------ tiendas
STORE = stores.Store(id="t", name="Tienda", platform="vtex", host="www.tienda.com.ar", categories=("informatica",))


def test_registro_filtra_por_categoria():
    reg = stores.load()
    ids = {s.id for s in reg.for_categories(["cuidado-personal"])}
    assert "fravega" in ids and "comeros" not in ids
    assert all(s.platform in search.PLATFORMS for s in reg.stores)


def test_parse_vtex_vendedor_mas_barato_con_stock():
    prods = [{"productName": "Mouse X", "link": "https://t/mouse-x/p", "items": [{"sellers": [
        {"commertialOffer": {"Price": 30000, "ListPrice": 40000, "AvailableQuantity": 3,
                             "Installments": [{"NumberOfInstallments": 6, "InterestRate": 0}]}},
        {"commertialOffer": {"Price": 25000, "ListPrice": 25000, "AvailableQuantity": 0}},
    ]}]}]
    [o] = search.parse_vtex(prods, STORE)
    assert (o["price"], o["list"], o["cuotas"], o["store_name"]) == (30000.0, 40000.0, 6, "Tienda")


def test_parse_woocommerce_y_tiendanube():
    woo = [{"name": "Mouse &amp; Pad", "permalink": "u", "is_in_stock": True,
            "prices": {"price": "1500000", "regular_price": "1800000", "currency_minor_unit": 2}}]
    [w] = search.parse_woocommerce(woo, STORE)
    assert (w["title"], w["price"], w["list"]) == ("Mouse & Pad", 15000.0, 18000.0)
    html = ('<div class="js-item-product"><a href="https://t/productos/mouse/" title="Mouse G">'
            '<div class="item-name">Mouse G</div><div data-variants="[{&quot;available&quot;:true,'
            '&quot;price_number&quot;:50000,&quot;price_with_payment_discount_short&quot;:&quot;$45.000&quot;}]">'
            '</div></a></div>')
    [t] = search.parse_tiendanube(html, STORE)
    assert (t["title"], t["price"], t["list"]) == ("Mouse G", 45000.0, 50000)


def test_search_all_sigue_si_una_tienda_falla_y_excluye():
    ok = stores.Store(id="ok", name="OK", platform="woocommerce", host="ok.com", categories=())
    bad = stores.Store(id="bad", name="Mala", platform="woocommerce", host="bad.com", categories=())

    def handler(req):
        if req.url.host == "bad.com":
            return httpx.Response(403)
        return httpx.Response(200, json=[
            {"name": "Mouse Logitech", "permalink": "a", "prices": {"price": "20000"}},
            {"name": "Mouse Logitech usado", "permalink": "b", "prices": {"price": "9000"}},
            {"name": "Pad para mouse", "permalink": "c", "prices": {"price": "5000"}}])

    rep = search.search_all([ok, bad], ["mouse", "mouse logitech"], exclude=["pad"],
                            client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert [x["title"] for x in rep.listings] == ["Mouse Logitech"]   # sin repetidos, usados ni pads
    st = {s["id"]: s for s in rep.stores}
    assert st["ok"]["ok"] and not st["bad"]["ok"] and "403" in st["bad"]["error"]


def test_links_manuales():
    assert search.mercadolibre_url("Secador de pelo iónico") == "https://listado.mercadolibre.com.ar/secador-de-pelo-ionico"
    assert "keyword=" in search.store_search_url(stores.Store("fravega", "F", "vtex", "www.fravega.com", ()), "x")


# ------------------------------------------------------------------ candidatos
def _listing(i, title, price, store="a"):
    return {"store": store, "store_name": store.upper(), "title": title, "price": price, "list": None,
            "cuotas": None, "promo": None, "url": f"https://{store}/{i}"}


def test_prefilter_relevancia_y_tope():
    prof = {"budget": {"max_ars": 50000}}
    ls = [_listing(1, "Mouse inalámbrico Logitech", 30000), _listing(2, "Secador de goma", 3000),
          _listing(3, "Mouse inalámbrico Razer", 90000)]
    assert [x["title"] for x in candidates.prefilter(ls, prof, ["mouse inalambrico"])] == ["Mouse inalámbrico Logitech"]


def test_agrupa_por_modelo_y_arma_rangos():
    ev = []
    for i, (model, price, score) in enumerate([("A1", 10, 80), ("A2", 12, 70), ("B1", 20, 90), ("B2", 25, 60),
                                                ("C1", 40, 95), ("C2", 45, 85), ("Malo", 5, 30)]):
        ev.append({**_listing(i, model, price * 1000), "model": model, "score": score,
                   "meets": [], "doubts": [], "reason": ""})
    ev.append({**_listing(99, "A1 otra tienda", 9000, store="b"), "model": "a1", "score": 80,
               "meets": [], "doubts": [], "reason": ""})
    models = candidates.group_by_model(ev, cap=42000)
    a1 = next(m for m in models if m["model"] == "A1")
    assert a1["best"]["price"] == 9000 and len(a1["offers"]) == 2
    tiers = candidates.build_tiers(models)
    assert [t["key"] for t in tiers] == ["economica", "equilibrada", "premium"]
    assert all(c["model"] != "Malo" for t in tiers for c in t["candidates"])
    assert next(m for m in models if m["model"] == "C2")["over_budget"]
    assert candidates.recommend(tiers) == "B1"


def test_find_completo_con_dobles(monkeypatch):
    def fake_search(shops, queries, exclude=None):
        assert {s.id for s in shops} >= {"fravega"} and "Philips BHD350" in queries
        return search.SearchReport(
            listings=[_listing(1, "Secador de pelo Philips BHD350", 60000), _listing(2, "Secador de pelo Gama", 40000)],
            stores=[{"id": "fravega", "name": "Frávega", "ok": True, "found": 2, "error": None}])

    monkeypatch.setattr(candidates, "search_all", fake_search)
    set_ai(FakeAI(evaluate={"items": [
        {"id": 0, "model": "Philips BHD350", "score": 88, "meets": ["Iónico"], "reason": "Cuida el pelo"},
        {"id": 1, "model": "Gama Tempo", "score": 40}, {"id": 7, "model": "fantasma", "score": 99}]}))
    prof = interview.normalize_profile({"product": "Secador de pelo", "category": "cuidado-personal",
                                        "search_queries": ["secador de pelo"], "suggested_models": ["Philips BHD350"]})
    out = candidates.find(prof)
    assert out["recommended"] == "Philips BHD350"
    assert out["counts"] == {"found": 2, "evaluated": 2, "matching": 1}
    assert out["links"]["mercadolibre"][0]["url"].endswith("/secador-de-pelo")


# ------------------------------------------------------------------ versión publicada
def test_codigo_de_acceso_y_limite(monkeypatch):
    from core import guard
    monkeypatch.setenv("ACCESS_CODE", "mate")
    monkeypatch.setattr(settings, "LIMIT_INTERVIEW_PER_HOUR", 2)
    monkeypatch.setattr(guard, "limiter", guard.RateLimiter())
    set_ai(DemoAI())
    c = TestClient(app)
    body = {"conversation": [{"role": "user", "content": "un mouse"}]}
    assert c.get("/api/status").json()["code_required"] is True
    r = c.post("/api/interview", json=body)
    assert r.status_code == 401 and r.json()["code_required"]
    assert c.post("/api/check-code", headers={"X-Access-Code": "otro"}).status_code == 401
    assert c.post("/api/check-code", headers={"X-Access-Code": "mate"}).status_code == 200
    ok = {"X-Access-Code": "mate", "X-Forwarded-For": "1.2.3.4, 10.0.0.1"}
    assert c.post("/api/interview", json=body, headers=ok).status_code == 200
    assert c.post("/api/interview", json=body, headers=ok).status_code == 200
    r = c.post("/api/interview", json=body, headers=ok)
    assert r.status_code == 429 and "límite" in r.json()["error"]
    # Otra persona (otra IP) tiene su propio cupo
    assert c.post("/api/interview", json=body, headers={**ok, "X-Forwarded-For": "5.6.7.8"}).status_code == 200


def test_rate_limiter_libera_con_el_tiempo():
    from core.guard import Denied, RateLimiter
    rl = RateLimiter(window_s=60)
    rl.hit("a", "x", 1, now=0)
    with pytest.raises(Denied):
        rl.hit("a", "x", 1, now=30)
    rl.hit("a", "x", 1, now=61)


# ------------------------------------------------------------------ API
def test_api_entrevista_y_errores(monkeypatch):
    monkeypatch.delenv("ACCESS_CODE", raising=False)
    set_ai(DemoAI())
    c = TestClient(app)
    assert c.get("/api/status").json()["stores"] == len(stores.load().stores)
    r = c.post("/api/interview", json={"conversation": [{"role": "user", "content": "un mouse"}]})
    assert r.status_code == 200 and r.json()["type"] == "question"
    set_ai(FakeAI(interview={"type": "question", "question": ""}))
    r = c.post("/api/interview", json={"conversation": [{"role": "user", "content": "un mouse"}]})
    assert r.status_code == 400 and "pregunta" in r.json()["error"]
    assert c.get("/").status_code == 200


# ------------------------------------------------------------------ Fase 1: búsquedas y resultados
def test_perfil_saca_modelos_y_frases_largas_de_las_busquedas():
    p = interview.normalize_profile({"product": "Mouse", "category": "informatica",
                                     "search_queries": ["mouse inalambrico", "Logitech G502",
                                                        "mouse inalambrico con muchos botones para trabajar"],
                                     "suggested_models": ["Logitech G502", "Razer Naga V2 Pro"]})
    assert p["search_queries"] == ["mouse inalambrico"]
    assert p["suggested_models"] == ["Logitech G502", "Razer Naga V2 Pro"]


def test_query_match_exige_el_codigo_del_modelo():
    assert candidates.query_match({"title": "Mouse Logitech G305"}, "Logitech G502") == 0.0
    assert candidates.query_match({"title": "Mouse Logitech G502 X"}, "Logitech G502") == 1.0
    assert candidates.query_match({"title": "Mouse Gamer Redragon"}, "mouse inalambrico") == 0.5


def test_spread_reparte_por_precio_segun_presupuesto():
    items = [_listing(i, f"mouse {i}", (i + 1) * 1000) for i in range(10)]
    prices = lambda xs: [x["price"] for x in xs]  # noqa: E731
    assert prices(candidates.spread(items, 3, "medio")) == [1000, 5000, 10000]
    assert max(prices(candidates.spread(items, 3, "economico"))) <= 6000
    assert min(prices(candidates.spread(items, 3, "alto"))) >= 5000


def test_prefilter_da_lugar_a_cada_modelo_sugerido(monkeypatch):
    monkeypatch.setattr(settings, "MAX_LISTINGS_TO_EVALUATE", 10)
    baratos = [_listing(i, f"Mouse inalambrico generico {i}", 5000 + i) for i in range(40)]
    g502 = _listing(99, "Mouse Gamer Inalambrico Logitech G502 Lightspeed", 150000)
    pool = candidates.prefilter(baratos + [g502], {"budget": {"level": "alto"}},
                                ["mouse inalambrico"], ["Logitech G502 Lightspeed"])
    assert pool[0] is g502 and len(pool) == 10


def test_rangos_por_precio_juntan_precios_parecidos():
    def m(name, price):
        return {"model": name, "score": 80, "best": {"price": price}}
    tiers = candidates.build_tiers([m("A", 16500), m("B", 32000), m("C", 41600), m("D", 44600),
                                    m("Trust", 65200), m("Lift", 66000), m("MX", 118000)])
    where = {c["model"]: t["key"] for t in tiers for c in t["candidates"]}
    assert where["Trust"] == where["Lift"]
    assert candidates.recommend(tiers, "alto") in {c["model"] for t in tiers if t["key"] == "premium" for c in t["candidates"]}


def test_casi_cumplen():
    ms = [{"model": x, "score": s, "best": {"price": 1}} for x, s in [("a", 90), ("b", 50), ("c", 40), ("d", 20)]]
    assert [m["model"] for m in candidates.near_misses(ms)] == ["b", "c"]


def test_usados_y_outlet_no_entran():
    assert search.is_used("Mouse Logitech OUTLET caja abierta")
    assert search.is_used("Secador Philips (exhibición)")
    assert not search.is_used("Mouse Logitech G502 nuevo")


def test_feedback_por_telegram(monkeypatch):
    from core import feedback
    sent = {}

    def handler(req):
        sent["url"] = str(req.url)
        sent["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True})

    fb = {"useful": False, "comment": "faltó el G502", "product": "Mouse", "recommended": "Razer Naga",
          "matching": 1, "queries": ["mouse inalambrico"]}
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert feedback.send(fb) is False
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    assert feedback.send(fb, httpx.Client(transport=httpx.MockTransport(handler))) is True
    assert sent["url"].endswith("/botT/sendMessage") and sent["body"]["chat_id"] == "42"
    assert "👎" in sent["body"]["text"] and "faltó el G502" in sent["body"]["text"]


def test_api_feedback(monkeypatch):
    monkeypatch.delenv("ACCESS_CODE", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    r = TestClient(app).post("/api/feedback", json={"useful": True, "comment": "genial", "product": "Mouse"})
    assert r.status_code == 200 and r.json() == {"ok": True, "sent": False}


def test_gemini_saltea_modelos_con_limite_por_un_rato(monkeypatch):
    from core import ai as ai_mod
    monkeypatch.setattr(ai_mod, "RETRY_WAIT_S", 0)
    monkeypatch.setattr(ai_mod, "_cooldown", {})
    monkeypatch.setattr(ai_mod, "_cool_reason", {})
    pedidos = []

    def handler(req: httpx.Request):
        modelo = req.url.path.split("/")[-1].split(":")[0]
        pedidos.append(modelo)
        if modelo == "lleno":
            return httpx.Response(429, json={"error": {"details": [{"retryDelay": "20s"}]}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]})

    gem = GeminiAI("k", "lleno,bueno", httpx.Client(transport=httpx.MockTransport(handler)))
    assert gem.json("s", [{"role": "user", "content": "x"}], "interview") == {"ok": 1}
    assert gem.json("s", [{"role": "user", "content": "x"}], "interview") == {"ok": 1}
    assert pedidos == ["lleno", "bueno", "bueno"]   # la segunda vez ni lo intenta
    solo_lleno = GeminiAI("k", "lleno", httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AIError, match="límite gratis"):
        solo_lleno.json("s", [{"role": "user", "content": "x"}], "interview")
    assert pedidos[-1] == "bueno"   # "lleno" sigue en pausa: no se volvió a pedir


# ------------------------------------------------------------------ ComparaYa (Mercado Libre)
def test_comparaya_direcciones_y_ofertas_exactas():
    from core import comparaya
    assert comparaya.slug_candidates("Monitor para oficina", "MSI PRO MP241", "Monitor MSI PRO MP241 24 pulgadas") == [
        "monitor-msi-pro-mp241-24", "monitor-msi-pro-mp241", "monitor-msi-mp241-24", "monitor-msi-mp241"]
    assert comparaya.slug_candidates("Mouse", "Logitech G502 Lightspeed")[0] == "mouse-logitech-g502-lightspeed"
    page = ('<h2>Precios por tienda</h2><ol>'
            '<li><span>Mercado Libre</span><p title="Mouse Logitech G502 Lightspeed">x</p><p>$<!-- -->124.804</p>'
            '<a href="https://www.mercadolibre.com.ar/x">ir</a></li>'
            '<li><span>Amonpul</span><p title="Mouse Logitech G502 HERO">x</p><p>$<!-- -->50.000</p>'
            '<a href="https://amonpul/y">ir</a></li>'
            '<li><span>Otra</span><p title="Mouse Logitech G502 Lightspeed usado">x</p><p>$<!-- -->40.000</p>'
            '<a href="https://otra/z">ir</a></li></ol>')
    offers = comparaya.exact_offers(page, "Logitech G502 Lightspeed")
    assert [(o["shop"], o["price"]) for o in offers] == [("Mercado Libre", 124804.0)]


def test_api_ml(monkeypatch):
    from core import comparaya
    monkeypatch.delenv("ACCESS_CODE", raising=False)
    monkeypatch.setattr(comparaya, "find", lambda model, product, title="": {"found": True, "page_url": "u", "ml": [], "others": []})
    r = TestClient(app).post("/api/ml", json={"model": "Logitech G502 Lightspeed", "product": "Mouse"})
    assert r.status_code == 200 and r.json()["search_url"].endswith("/logitech-g502-lightspeed")


def test_vtex_frávega_suma_iva_y_arma_el_link_de_su_web():
    fravega = stores.Store(id="fravega", name="Frávega", platform="vtex", host="www.fravega.com", categories=())
    prods = [{"productName": "HP Gamer Core I7 1355U 16GB RAM 1TB SSD Notebook 15,6 FHD Silver",
              "link": "https://www.fravega.com/hp-gamer-990495101/p", "items": [{"itemId": "990249245", "sellers": [
                  {"commertialOffer": {"Price": 1979418.1, "Tax": 207838.9, "ListPrice": 5557200,
                                       "AvailableQuantity": 10}}]}]}]
    [o] = search.parse_vtex(prods, fravega)
    assert o["price"] == pytest.approx(2187257.0)
    assert o["url"] == "https://www.fravega.com/p/hp-gamer-core-i7-1355u-16gb-ram-1tb-ssd-notebook-15-6-fhd-silver-990249245/"
    [otra] = search.parse_vtex(prods, STORE)   # otras tiendas VTEX: el link de la API
    assert otra["url"] == "https://www.fravega.com/hp-gamer-990495101/p"


def test_si_contradice_un_obligatorio_el_puntaje_se_limita():
    set_ai(FakeAI(evaluate={"items": [{"id": 0, "model": "MSI Ryzen 5 RTX 4060", "score": 90,
                                       "fails_must": ["Ryzen 7 o superior"], "doubts": ["x"]}]}))
    prof = interview.normalize_profile({"product": "Notebook", "category": "informatica",
                                        "must": ["Ryzen 7 o superior"], "search_queries": ["notebook"]})
    [e] = candidates.evaluate([_listing(0, "Notebook MSI Ryzen 5 RTX 4060", 2_000_000)], prof)
    assert e["score"] == candidates.FAIL_CAP and e["doubts"][0] == "No cumple: Ryzen 7 o superior"
