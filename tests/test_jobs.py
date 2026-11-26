"""Búsqueda en segundo plano con progreso (core/jobs.py, endpoints /api/candidates y find(progress=...))."""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from core import candidates, interview, jobs, search
from core.ai import AIError, set_ai


@pytest.fixture(autouse=True)
def _limpio():
    yield
    set_ai(None)


def esperar(job, segundos=3.0):
    fin = time.time() + segundos
    while job.status == "running" and time.time() < fin:
        time.sleep(0.01)
    return job


# ------------------------------------------------------------------ el administrador de trabajos
def test_el_trabajo_corre_en_segundo_plano_y_guarda_el_progreso():
    m = jobs.JobManager(max_running=2)

    def trabajo(progress):
        progress("tiendas", "21 tiendas")
        progress("evaluar", "tanda 1 de 5")
        progress("evaluar", "tanda 2 de 5")
        return {"ok": 1}

    job = esperar(m.start(trabajo))
    snap = job.snapshot()
    assert snap["status"] == "done" and snap["result"] == {"ok": 1}
    assert snap["seen"] == ["tiendas", "evaluar"]                       # cada paso una sola vez, en orden
    assert (snap["stage"], snap["detail"]) == ("evaluar", "tanda 2 de 5")
    assert m.get(job.id) is job and m.get("inventado") is None


def test_un_error_pensado_para_la_persona_se_muestra_y_uno_inesperado_no():
    m = jobs.JobManager()
    a = esperar(m.start(lambda p: (_ for _ in ()).throw(AIError("Llegamos al límite gratis de la IA"))))
    assert a.snapshot() == {**a.snapshot(), "status": "error", "error": "Llegamos al límite gratis de la IA"}
    b = esperar(m.start(lambda p: 1 / 0))
    assert b.snapshot()["error"] == jobs.GENERIC_ERROR                     # nada de trazas ni "division by zero"
    assert "result" not in b.snapshot()


def test_no_se_corren_mas_busquedas_a_la_vez_que_el_limite_y_se_libera_al_terminar():
    m = jobs.JobManager(max_running=1)
    suelta = threading.Event()
    primera = m.start(lambda p: suelta.wait(3) and {"ok": 1})
    with pytest.raises(jobs.Busy):
        m.start(lambda p: {"ok": 2})
    suelta.set()
    esperar(primera)
    assert esperar(m.start(lambda p: {"ok": 3})).snapshot()["result"] == {"ok": 3}


def test_los_trabajos_terminados_se_descartan_con_el_tiempo():
    m = jobs.JobManager(ttl_s=60)
    viejo = esperar(m.start(lambda p: {}))
    viejo.finished = time.time() - 120
    nuevo = m.start(lambda p: {})
    assert m.get(viejo.id) is None and m.get(nuevo.id) is not None


# ------------------------------------------------------------------ find() avisa en qué paso va
def test_find_reporta_cada_paso_en_orden(monkeypatch):
    class Ev:
        name = "ev"

        def json(self, system, messages, task):
            ids = [int(linea.split(" | ")[0]) for linea in messages[0]["content"].split("PUBLICACIONES\n")[1].splitlines()]
            return {"items": [{"id": i, "model": f"Mouse {i}", "score": 80} for i in ids]}

    def lista(i):
        return {"store": "a", "store_name": "A", "title": f"Mouse inalambrico {i}", "price": 10000 + i,
                "list": None, "cuotas": None, "promo": None, "url": f"https://a/{i}"}

    monkeypatch.setattr(candidates, "search_all", lambda *a, **k: search.SearchReport(
        listings=[lista(i) for i in range(5)], stores=[{"id": "a", "name": "A", "ok": True, "found": 5, "error": None}]))
    monkeypatch.setattr(candidates.comparaya, "find", lambda *a, **k: {"found": False, "ml": [], "others": []})
    set_ai(Ev())
    perfil = interview.normalize_profile({"product": "Mouse", "category": "informatica", "search_queries": ["mouse inalambrico"]})
    avisos = []
    out = candidates.find(perfil, None, lambda etapa, detalle="": avisos.append((etapa, detalle)))
    assert out["counts"]["found"] == 5
    vistos = list(dict.fromkeys(e for e, _ in avisos))
    assert vistos == [clave for clave, _ in candidates.STAGES]                   # los 5 pasos, en el orden de STAGES
    assert ("evaluate", "5 publicaciones · tanda 1 de 1") in avisos               # el avance dentro de la evaluación
    assert avisos[0][0] == "stores" and "tiendas" in avisos[0][1]


# ------------------------------------------------------------------ endpoints
@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.delenv("ACCESS_CODE", raising=False)
    monkeypatch.setattr(jobs, "manager", jobs.JobManager(max_running=1))

    def falsa(profile, postal_code, progress):
        progress("stores", "21 tiendas")
        progress("tiers", "")
        return {"tiers": [], "near": [], "postal_code": postal_code}
    monkeypatch.setattr(candidates, "find", falsa)
    return TestClient(app)


def test_la_busqueda_se_arranca_y_se_consulta(cliente):
    r = cliente.post("/api/candidates", json={"profile": {"product": "Mouse", "category": "informatica"},
                                              "postal_code": "3000"})
    assert r.status_code == 200
    cuerpo = r.json()
    assert [s["key"] for s in cuerpo["stages"]] == [k for k, _ in candidates.STAGES]
    assert all(s["label"] for s in cuerpo["stages"])
    fin = time.time() + 3
    while time.time() < fin:
        snap = cliente.get(f"/api/candidates/{cuerpo['job_id']}").json()
        if snap["status"] != "running":
            break
        time.sleep(0.01)
    assert snap["status"] == "done" and snap["result"]["postal_code"] == "3000"
    assert snap["seen"] == ["stores", "tiers"] and snap["elapsed_s"] >= 0


def test_una_busqueda_que_no_existe_da_404_con_un_mensaje_claro(cliente):
    r = cliente.get("/api/candidates/inventada")
    assert r.status_code == 404 and r.json()["gone"] is True and "Probá de nuevo" in r.json()["error"]


def test_con_demasiadas_busquedas_a_la_vez_responde_429(cliente, monkeypatch):
    suelta = threading.Event()
    monkeypatch.setattr(candidates, "find", lambda profile, cp, progress: suelta.wait(3) and {"tiers": []})
    pedido = {"profile": {"product": "Mouse", "category": "informatica"}}
    assert cliente.post("/api/candidates", json=pedido).status_code == 200
    r = cliente.post("/api/candidates", json=pedido)
    assert r.status_code == 429 and "en curso" in r.json()["error"]
    suelta.set()


def test_consultar_el_avance_tambien_pide_el_codigo_de_acceso(cliente, monkeypatch):
    job = jobs.manager.start(lambda p: {"tiers": []})
    esperar(job)
    monkeypatch.setenv("ACCESS_CODE", "mate")
    assert cliente.get(f"/api/candidates/{job.id}").status_code == 401
    assert cliente.get(f"/api/candidates/{job.id}", headers={"X-Access-Code": "mate"}).status_code == 200


def test_dos_configuraciones_que_solo_difieren_en_un_digito_no_se_confunden():
    assert candidates.title_key("Notebook Asus TUF Ryzen 7 16GB") != candidates.title_key("Notebook Asus TUF Ryzen 5 16GB")
    assert candidates.title_key("Notebook Asus TUF Ryzen 7 16GB") == candidates.title_key("NOTEBOOK ASUS TUF RYZEN 7 16GB")
    a = {"store": "a", "store_name": "A", "title": "Notebook Ryzen 7", "price": 2, "list": None, "cuotas": None, "promo": None, "url": "u1"}
    b = {**a, "title": "Notebook Ryzen 5", "url": "u2"}
    assert len(candidates.prefilter([a, b], {"budget": {}}, ["notebook ryzen"])) == 2      # no se descarta ninguna
