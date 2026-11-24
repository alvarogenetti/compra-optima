"""Fase 2: seguir precio, bot de Telegram y escaneo programado (sin red, base en memoria)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from app.main import app
from core import db, follow, market, search, telegram, watch


@pytest.fixture(autouse=True)
def memdb():
    db.set_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
    yield
    db.set_engine(None)


def msg(text, chat=1, name="Ana"):
    return {"chat": {"id": chat, "type": "private", "first_name": name}, "text": text}


BEST = {"price": 100000, "store_name": "Frávega", "url": "https://f/x"}


def test_vincular_listar_y_dejar():
    tok = follow.create("Logitech G502 Lightspeed", "informatica", "Mouse", BEST)
    assert "Listo" in follow.handle(msg(f"/start {tok}"))
    assert "Ya estabas" in follow.handle(msg(f"/start {tok}"))
    # Otra persona abre el mismo link: tiene su propio seguimiento
    assert "Listo" in follow.handle(msg(f"/start {tok}", chat=2, name="Beto"))
    assert len(follow.active_for_chat("2")) == 1
    lista = follow.handle(msg("/lista"))
    assert "Logitech G502 Lightspeed" in lista and "$100.000" in lista
    fid = follow.active_for_chat("1")[0]["id"]
    assert "dejé de seguir" in follow.handle(msg(f"/dejar_{fid}"))
    assert follow.active_for_chat("1") == [] and len(follow.active_for_chat("2")) == 1
    assert "ya no sirve" in follow.handle(msg("/start inventado"))
    assert "Compra Óptima" in follow.handle(msg("hola"))
    assert follow.handle({"chat": {"id": 5, "type": "group"}, "text": "/lista"}) is None


def test_maximo_de_seguimientos(monkeypatch):
    monkeypatch.setattr(follow, "MAX_FOLLOWS_PER_CHAT", 1)
    follow.handle(msg(f"/start {follow.create('A 1', 'informatica', '', BEST)}"))
    assert "máximo" in follow.handle(msg(f"/start {follow.create('B 2', 'informatica', '', BEST)}"))


def test_mediana_necesita_historia():
    now = datetime(2026, 12, 1, tzinfo=timezone.utc)
    pts = [(now - timedelta(days=d), 100.0 + d) for d in range(2, 10)]
    ref, n = market.median_reference(pts, now)
    assert n == 8 and ref == pytest.approx(105.5)
    assert market.median_reference(pts[:3], now)[0] is None
    assert market.reference([], seed=90, now=now) == (90, "precio de cuando empezaste a seguirlo")


def test_matches_pide_el_modelo_en_el_titulo():
    assert watch.matches({"title": "Mouse Logitech G502 Lightspeed Wireless"}, "Logitech G502 Lightspeed")
    assert not watch.matches({"title": "Mouse Logitech G305 Lightspeed"}, "Logitech G502 Lightspeed")
    assert watch.matches({"title": "Secador de pelo Gama Tempo 2200W"}, "Gama Tempo")
    assert not watch.matches({"title": "Secador Gama Elegance"}, "Gama Tempo")
    assert not watch.matches({"title": "Mouse c/Cable Logitech G502 HERO Negro"}, "Logitech G502 Lightspeed")
    assert not watch.matches({"title": "Cable USB Goapongs Para Logitech G502 Lightspeed"}, "Logitech G502 Lightspeed")
    assert not watch.matches({"title": "Funda Logitech G502 Lightspeed"}, "Logitech G502 Lightspeed", "Mouse")
    assert watch.matches({"title": "Secador De Pelo Philips Thermo Protect Bhd350/10 2100w"}, "Philips BHD350")
    assert watch.matches({"title": "Mouse Inalambrico LOGITECH G502 Lightspeed Negro"}, "Logitech G502 Lightspeed")


def _listing(price, store="fravega", title="Mouse Logitech G502 Lightspeed", lst=None):
    return {"store": store, "store_name": store.capitalize(), "title": title, "price": price, "list": lst,
            "cuotas": 6, "promo": None, "url": f"https://{store}/g502"}


def test_escaneo_avisa_ofertas_reales_sin_repetir(monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send", lambda chat, text, client=None: sent.append((chat, text)) or True)
    tok = follow.create("Logitech G502 Lightspeed", "informatica", "Mouse", BEST)
    follow.handle(msg(f"/start {tok}"))
    old = follow.create("Viejo 1", "informatica", "", BEST)  # nunca se vinculó
    with db.engine().begin() as c:
        c.execute(db.follows.update().where(db.follows.c.token == old)
                  .values(created_at=db.now() - timedelta(days=10)))

    listings = [_listing(100000), _listing(105000, "oncity"), _listing(5000, title="Pad Logitech")]
    monkeypatch.setattr(watch, "search_all",
                        lambda shops, queries, exclude=None, client=None: search.SearchReport(listings=list(listings)))
    now = db.now()
    # Primer escaneo: sólo fija el punto de partida
    assert watch.run(now=now) == {"models": 1, "found": 1, "alerts": 0, "notes": 0}
    listings[0] = _listing(80000, lst=200000)
    now += timedelta(hours=3)
    assert watch.run(now=now) == {"models": 1, "found": 1, "alerts": 1, "notes": 0}
    chat, text = sent[-1]
    assert chat == "1" and "Buena oferta" in text and "$80.000" in text and "20 %" in text
    assert "la tienda dice -60 %" in text and "/dejar_" in text
    # La misma oferta no se repite...
    assert watch.run(now=now + timedelta(hours=3))["alerts"] == 0
    # ...salvo que mejore al menos 3 %
    listings[0] = _listing(70000)
    assert watch.run(now=now + timedelta(hours=6))["alerts"] == 1
    assert "Excepcional" in sent[-1][1] or "excepcional" in sent[-1][1]
    with db.engine().connect() as c:
        stores_seen = {r.store for r in c.execute(select(db.prices.c.store))}
        assert stores_seen == {"fravega", "oncity"}
        assert c.execute(select(db.follows).where(db.follows.c.token == old)).first() is None


def test_sin_baja_real_no_avisa(monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send", lambda chat, text, client=None: sent.append(text) or True)
    follow.handle(msg(f"/start {follow.create('Logitech G502 Lightspeed', 'informatica', '', BEST)}"))
    monkeypatch.setattr(watch, "search_all", lambda *a, **k: search.SearchReport(listings=[_listing(92000)]))
    assert watch.run()["alerts"] == 0 and sent == []


def test_api_seguir_y_webhook(monkeypatch):
    c = TestClient(app)
    monkeypatch.delenv("ACCESS_CODE", raising=False)
    monkeypatch.delenv("COMPRA_BOT_TOKEN", raising=False)
    body = {"model": "Logitech G502 Lightspeed", "category": "informatica", "product": "Mouse", "best": BEST}
    assert c.get("/api/status").json()["follow_enabled"] is False
    assert c.post("/api/follow", json=body).status_code == 400

    monkeypatch.setenv("COMPRA_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(telegram, "bot_username", lambda: "CompraOptimaBot")
    assert c.get("/api/status").json()["follow_enabled"] is True
    r = c.post("/api/follow", json=body)
    assert r.status_code == 200 and r.json()["link"].startswith("https://t.me/CompraOptimaBot?start=")
    tok = r.json()["link"].split("start=")[1]
    assert c.post("/api/follow", json={**body, "category": "inventada"}).status_code == 400

    replies = []
    monkeypatch.setattr(telegram, "send", lambda chat, text, client=None: replies.append((chat, text)) or True)
    assert c.post("/api/telegram/mal", json={"message": msg(f"/start {tok}")}).status_code == 404
    r = c.post(f"/api/telegram/{telegram.webhook_secret()}", json={"message": msg(f"/start {tok}", chat=77)})
    assert r.status_code == 200 and replies[-1][0] == 77 and "Listo" in replies[-1][1]
    assert len(follow.active_for_chat("77")) == 1


def test_database_url_de_neon(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@ep-x.neon.tech/neondb?sslmode=require")
    assert db.database_url() == "postgresql+psycopg://u:p@ep-x.neon.tech/neondb?sslmode=require"
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    assert db.database_url().startswith("postgresql+psycopg://")


def test_primer_escaneo_mas_barato_es_un_dato_no_una_baja(monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send", lambda chat, text, client=None: sent.append(text) or True)
    follow.handle(msg(f"/start {follow.create('MSI PRO MP241', 'informatica', 'Monitor', {'price': 474962, 'store_name': 'Frávega', 'url': 'u'})}"))
    monkeypatch.setattr(watch, "search_all", lambda *a, **k: search.SearchReport(listings=[
        _listing(293716, title="Monitor MSI PRO MP241 E14V IPS 24 pulgadas FHD")]))
    out = watch.run()
    assert out["alerts"] == 0 and out["notes"] == 1
    assert "No es una baja de precio" in sent[0] and "$293.716" in sent[0] and "$474.962" in sent[0]
    assert follow.active_for_chat("1")[0]["start_price"] == 293716
    # El escaneo siguiente al mismo precio no avisa nada
    assert watch.run(now=db.now() + timedelta(hours=3))["alerts"] == 0 and len(sent) == 1
