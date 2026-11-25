"""Entrevista: de "quiero un mouse" a un perfil de compra explicado.

La conversación la guarda el navegador y viene completa en cada pedido (el servidor no guarda
estado). Los turnos de la IA se guardan como el JSON que devolvió, así ve sus propias preguntas.

Cada paso devuelve:
  {"type": "question", "explanation", "question", "help", "options", "multi", "draft"}
o {"type": "profile", "draft", "profile": {...}}   (ver PROFILE_SHAPE en el prompt)
"""
from __future__ import annotations

import json

from core import settings, stores
from core.ai import AIError, get_ai

SYSTEM = """Sos el asesor de compras de "Compra Óptima", una web argentina. Ayudás a gente que NO sabe
de tecnología a elegir el producto ideal para su uso y después a encontrarlo al mejor precio.
Hablás en español rioplatense (vos), cálido, claro y breve.

CÓMO ENTREVISTAR
- Antes de la primera pregunta, pensá (sin mostrarlo) cuáles son los 5 a 7 FACTORES DE DECISIÓN que más
  cambian qué producto conviene en esa categoría, como lo haría un vendedor experto y honesto. Ejemplos:
  · Monitor: para qué lo usa (texto, juegos, diseño), tamaño y resolución (24" Full HD / 27" 2K / 4K),
    qué tan fluido (60/75 Hz alcanza para oficina; 144 Hz o más si juega), plano o curvo, con qué lo
    conecta (notebook con USB-C, PC, consola), si lo va a poner en un brazo (VESA) o necesita ajuste de
    altura, uno o dos monitores, marca preferida.
  · Mouse: uso, tamaño de mano y forma de agarre, inalámbrico o cable, cantidad de botones, ruido, peso.
  · Secador de pelo: tipo y largo de pelo, frecuencia de uso, si viaja, potencia, función iónica.
  Después recorré esos factores con preguntas, de lo más importante a lo menos, hasta cubrirlos.
- Hacé UNA pregunta por vez (nunca juntes dos temas en la misma). Entre 5 y {max_q} preguntas en total. No cierres antes de cubrir los factores
  que cambian la recomendación: una entrevista corta da un perfil genérico.
- Preguntá en el idioma de la persona: por el USO y las consecuencias prácticas. Cuando una decisión es
  técnica (resolución, panel, switches), presentala con lo que cambia en la vida real y una recomendación.
  Bien: "¿Qué tan nítido querés el texto? · Full HD (más barato, bien en 24") · 2K (texto más nítido,
  ideal en 27", recomendado si trabajás muchas horas) · 4K (para diseño o pantallas grandes)".
  Bien: "¿Te molesta que haga ruido al hacer clic?". Mal: "¿Qué switches preferís?", "¿Cuántos DPI?".
- Si tiene sentido en la categoría, preguntá también por marca preferida o a evitar (con la opción
  "Me da igual") y, antes de cerrar, "¿Hay algo que sí o sí quieras, o que no quieras?".
- Si la persona ya escribió muchos detalles al principio, no los vuelvas a preguntar, pero igual
  preguntá por los factores que faltan (uso principal, tamaño o portabilidad, marca, presupuesto si no lo
  dio, "¿algo más que sí o sí?"): al menos 3 preguntas antes del perfil. Podés confirmar en "draft" lo
  que ya dijo.
- No preguntes lo que la persona ya dijo (ni lo que se deduce claramente). Si algo no aplica por lo que
  contó (curvo para alguien que sólo hace planillas), podés saltearlo, pero si hay duda, preguntá.
- Ofrecé de 2 a 5 opciones cortas que cubran los casos comunes. La persona también puede escribir libre.
- Usá "multi": true sólo si tiene sentido elegir varias opciones a la vez (por ejemplo, usos).
- En algún momento preguntá por el presupuesto, con opciones del estilo "Lo más barato que sirva bien",
  "Un precio medio", "Pago más si vale la pena", "Tengo un tope (escribilo)". NO inventes montos en pesos:
  los precios en Argentina cambian mucho y los rangos reales salen de las tiendas.
- Si la persona responde "no sé" o pide que le expliques: completá "explanation" con 2 a 4 oraciones simples,
  con ejemplos cotidianos, explicando las opciones y qué cambia en la práctica. Después repetí la pregunta
  (más simple si hace falta). Fuera de ese caso, "explanation" va vacío.
- "help" es una línea que dice por qué preguntás eso ("Así sabemos si conviene inalámbrico").
- "draft" es la lista de lo que ya sabés, en etiquetas cortas para mostrar en pantalla
  (ej: {{"label": "Uso", "value": "Juegos y trabajo"}}). Actualizala en cada turno.
- Si lo que escriben no es algo para comprar, explicá amablemente qué hacés y preguntá qué quieren comprar.

CUÁNDO TERMINAR
Cuando ya sepas lo suficiente (o la persona pida ver resultados), devolvé el perfil (type "profile").

EL PERFIL
- Todo lo que la persona pidió EXPLÍCITAMENTE con números o nombres (RAM, almacenamiento, procesador,
  placa de video, Hz, tamaño, marca) va en "must" tal cual lo pidió ("32 GB de RAM o más", "1 TB de
  almacenamiento o más"): no lo resumas ni lo omitas.
- "must": lo que SÍ o SÍ tiene que tener. "nice": lo deseable. "avoid_paying_for": cosas por las que
  NO conviene pagar extra para este uso (marketing, funciones que no va a usar).
- Cada característica lleva:
  - "feature": nombre corto ("Sensor de 16.000 DPI o más").
  - "what_is": qué es eso, en una oración que entienda cualquiera ("Los DPI miden cuánto se mueve el
    cursor por cada centímetro que movés el mouse").
  - "why": por qué le conviene A ESTA PERSONA, citando lo que te dijo ("Como me dijiste que jugás
    shooters, te sirve un sensor preciso para apuntar fino").
- "summary": 2 o 3 oraciones con el producto ideal en criollo.
- "category": UNA de estas categorías (la clave exacta):
{categories}
- "search_queries": de 2 a 3 búsquedas AMPLIAS para el buscador de una tienda argentina. El buscador
  busca esas palabras LITERALMENTE en los títulos de las publicaciones, y los títulos dicen el tipo de
  producto y datos concretos ("Mouse Gamer Inalámbrico Logitech G502 Lightspeed"), nunca necesidades.
  Por eso: tipo de producto + como mucho UNA palabra que aparezca en títulos reales.
  Bien: "mouse inalambrico", "mouse gamer inalambrico", "mouse vertical", "secador de pelo ionico".
  Mal: "mouse muchos botones", "mouse productivo", "mouse comodo para trabajar", "logitech g502".
  El filtro fino (cantidad de botones, comodidad, etc.) lo hacemos después leyendo cada publicación:
  acá conviene traer de más. Sin marcas salvo que la persona las pida, y sin modelos (van abajo).
- "suggested_models": de 4 a 8 modelos concretos y conocidos que cumplan el perfil y se vendan en
  Argentina, repartidos en distintos rangos de precio (algunos accesibles, algunos de gama media,
  alguno de gama alta), con marca y modelo ("Logitech G502 Lightspeed"). Sólo si estás seguro de que
  existen; se buscan en las tiendas y si no aparecen no se recomiendan.
- "spec_queries": si el pedido tiene especificaciones DURAS que aparecen en los títulos (modelo de placa
  de video, línea de procesador, RAM), una búsqueda corta por cada variante que cumple, de 0 a 6:
  tipo de producto + la especificación. Ej. "RTX 4050 para arriba" → ["notebook rtx 4050",
  "notebook rtx 4060", "notebook rtx 5050", "notebook rtx 5060"]; "Ryzen 7 o más" → ["notebook ryzen 7",
  "notebook ryzen 9"]. Así no se escapan publicaciones que las búsquedas amplias no traen. Si no hay
  especificaciones duras, lista vacía.
- "exclude_words": palabras de publicaciones que NO son el producto (accesorios, repuestos, combos que
  no pidió), por ejemplo ["pad", "funda", "repuesto"].
- "budget": {{"level": "economico" | "medio" | "alto" | "sin_definir", "max_ars": número o null (sólo si
  la persona dio un tope), "note": texto corto}}.

FORMATO: respondé SÓLO un objeto JSON, sin texto alrededor:
{{"type": "question", "explanation": "", "question": "...", "help": "...", "options": ["..."],
  "multi": false, "draft": [{{"label": "...", "value": "..."}}]}}
o
{{"type": "profile", "draft": [...], "profile": {{"product": "Mouse", "category": "informatica",
  "summary": "...", "must": [{{"feature": "", "what_is": "", "why": ""}}], "nice": [...],
  "avoid_paying_for": [{{"feature": "", "why": ""}}], "budget": {{...}}, "search_queries": [...],
  "suggested_models": [...], "spec_queries": [...], "exclude_words": [...]}}}}"""

FORCE_PROFILE = ("(Mensaje del sistema: ya se hicieron todas las preguntas permitidas. "
                 "Devolvé ahora el perfil con lo que sabés.)")
TOO_SOON = ("(Mensaje del sistema: todavía no armes el perfil. Hacé UNA pregunta sobre el factor de "
            "decisión más importante que falte; no repitas lo que la persona ya dijo.)")
WANTS_RESULTS = ("(Mensaje del sistema: la persona quiere ver resultados ya. "
                 "Devolvé el perfil con lo que sabés.)")


def system_prompt() -> str:
    return SYSTEM.format(max_q=settings.MAX_QUESTIONS, categories=stores.load().category_prompt())


def count_questions(conversation: list[dict]) -> int:
    n = 0
    for m in conversation:
        if m.get("role") != "assistant":
            continue
        try:
            t = json.loads(m["content"])
            # Una pregunta repetida con explicación ("no sé") no cuenta como pregunta nueva
            if t.get("type") == "question" and not t.get("explanation"):
                n += 1
        except (ValueError, AttributeError):
            n += 1
    return n


def step(conversation: list[dict], finish: bool = False) -> dict:
    """Un turno de la entrevista. `conversation` arranca con el texto inicial del usuario."""
    conv = [{"role": m["role"], "content": str(m["content"])} for m in conversation
            if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()]
    if not conv or conv[0]["role"] != "user":
        raise AIError("La conversación tiene que arrancar con lo que querés comprar.")
    asked = count_questions(conv)
    force = finish or asked >= settings.MAX_QUESTIONS
    if force:
        conv = conv + [{"role": "user", "content": WANTS_RESULTS if finish else FORCE_PROFILE}]
    out = normalize(get_ai().json(system_prompt(), conv, task="interview"))
    if out["type"] == "profile" and not force and asked < settings.MIN_QUESTIONS:
        # Cerró demasiado rápido (pasa cuando el primer mensaje es muy detallado): una pregunta más
        out = normalize(get_ai().json(system_prompt(), conv + [{"role": "user", "content": TOO_SOON}],
                                      task="interview"))
    if force and out["type"] != "profile":
        raise AIError("La IA no pudo armar el perfil. Probá de nuevo.")
    out["asked"] = asked + (1 if out["type"] == "question" else 0)
    out["max_questions"] = settings.MAX_QUESTIONS
    return out


def _s(v, limit: int = 600) -> str:
    return str(v or "").strip()[:limit]


def _features(items, with_what: bool = True) -> list[dict]:
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, str):
            it = {"feature": it}
        if not isinstance(it, dict) or not _s(it.get("feature")):
            continue
        f = {"feature": _s(it.get("feature"), 120), "why": _s(it.get("why"))}
        if with_what:
            f["what_is"] = _s(it.get("what_is"))
        out.append(f)
    return out[:10]


def _strings(items, n: int, limit: int = 80) -> list[str]:
    seen, out = set(), []
    for x in items if isinstance(items, list) else []:
        s = _s(x, limit)
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out[:n]


def _draft(items) -> list[dict]:
    out = []
    for d in items if isinstance(items, list) else []:
        if isinstance(d, dict) and _s(d.get("label")) and _s(d.get("value")):
            out.append({"label": _s(d["label"], 40), "value": _s(d["value"], 120)})
    return out[:12]


def normalize(raw: dict) -> dict:
    """Valida y recorta lo que devolvió la IA para que la interfaz nunca reciba basura."""
    draft = _draft(raw.get("draft"))
    if raw.get("type") == "profile" or isinstance(raw.get("profile"), dict) and not raw.get("question"):
        return {"type": "profile", "draft": draft, "profile": normalize_profile(raw.get("profile") or {})}
    question = _s(raw.get("question"), 300)
    if not question:
        raise AIError("La IA no devolvió una pregunta. Probá de nuevo.")
    return {
        "type": "question",
        "explanation": _s(raw.get("explanation"), 1200),
        "question": question,
        "help": _s(raw.get("help"), 200),
        "options": _strings(raw.get("options"), 6, 90),
        "multi": bool(raw.get("multi")),
        "draft": draft,
    }


def normalize_profile(p: dict) -> dict:
    reg = stores.load()
    cat = _s(p.get("category"), 40).lower()
    if cat not in reg.categories:
        cat = "informatica" if not reg.categories else next(iter(reg.categories))
    budget = p.get("budget") if isinstance(p.get("budget"), dict) else {}
    level = _s(budget.get("level"), 20).lower()
    try:
        max_ars = float(budget.get("max_ars")) if budget.get("max_ars") not in (None, "") else None
    except (TypeError, ValueError):
        max_ars = None
    product = _s(p.get("product"), 80) or "Producto"
    models = _strings(p.get("suggested_models"), 8)
    model_keys = {m.lower() for m in models}
    # Las búsquedas son amplias: sin modelos (van aparte) y de pocas palabras
    queries = [q for q in _strings(p.get("search_queries"), 4)
               if q.lower() not in model_keys and len(q.split()) <= 5][:3] or [product.lower()]
    return {
        "product": product,
        "category": cat,
        "summary": _s(p.get("summary"), 800),
        "must": _features(p.get("must")),
        "nice": _features(p.get("nice")),
        "avoid_paying_for": _features(p.get("avoid_paying_for"), with_what=False),
        "budget": {"level": level if level in ("economico", "medio", "alto") else "sin_definir",
                   "max_ars": max_ars if max_ars and max_ars > 0 else None,
                   "note": _s(budget.get("note"), 200)},
        "search_queries": queries,
        "suggested_models": models,
        "spec_queries": [q for q in _strings(p.get("spec_queries"), 6)
                         if len(q.split()) <= 4 and q.lower() not in {x.lower() for x in queries}],
        "exclude_words": _strings(p.get("exclude_words"), 15, 40),
    }
