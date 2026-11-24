"""Proveedor "demo": sin IA. Preguntas fijas y evaluación por palabras.

Sirve para probar la interfaz y la búsqueda real en tiendas sin clave de Gemini. Las respuestas
son genéricas a propósito: la gracia de la app está en la IA real.
"""
from __future__ import annotations

import json
import re
import unicodedata

QUESTIONS = [
    {"question": "¿Para qué lo vas a usar principalmente?",
     "help": "Con el uso sabemos qué características importan y cuáles son marketing.",
     "options": ["Trabajo o estudio", "Entretenimiento", "Uso diario en casa", "Uso intensivo / profesional"],
     "multi": True, "label": "Uso"},
    {"question": "¿Cuánto lo vas a usar?",
     "help": "Si lo usás mucho, conviene priorizar comodidad y durabilidad.",
     "options": ["Un rato por día", "Varias horas por día", "De vez en cuando"], "label": "Frecuencia"},
    {"question": "¿Qué te importa más?",
     "help": "Así sabemos dónde conviene gastar y dónde ahorrar.",
     "options": ["Que dure muchos años", "Que sea cómodo y fácil de usar", "Que sea lo más barato posible",
                 "Que tenga lo último"], "label": "Prioridad"},
    {"question": "¿Cuánto querés gastar?",
     "help": "Los rangos reales los sacamos de las tiendas.",
     "options": ["Lo más barato que sirva bien", "Un precio medio", "Pago más si vale la pena",
                 "Tengo un tope (escribilo)"], "label": "Presupuesto"},
]

CATEGORY_WORDS = {
    "informatica": ["mouse", "teclado", "monitor", "notebook", "impresora", "router", "pc", "webcam"],
    "gaming": ["consola", "joystick", "silla gamer", "playstation", "xbox", "nintendo"],
    "celulares": ["celular", "telefono", "tablet", "smartwatch", "reloj"],
    "audio-tv": ["auricular", "parlante", "tv", "televisor", "smart tv", "barra de sonido", "proyector"],
    "electrodomesticos": ["heladera", "lavarropas", "aire acondicionado", "cocina", "horno", "calefactor"],
    "pequenos-electro": ["cafetera", "licuadora", "freidora", "aspiradora", "pava", "microondas", "tostadora"],
    "cuidado-personal": ["secador", "secadora", "planchita", "afeitadora", "cortadora", "depiladora"],
    "hogar": ["colchon", "ventilador", "silla", "escritorio", "lampara"],
    "herramientas": ["taladro", "amoladora", "hidrolavadora", "atornillador"],
}

EXPLAIN = ("(Modo demo, sin IA) Elegí la opción que más se parezca a tu caso, no hay respuestas "
           "incorrectas. Con la IA activada, acá vas a ver una explicación de cada opción con ejemplos.")


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()


def guess_category(text: str) -> str:
    t = _fold(text)
    for cat, words in CATEGORY_WORDS.items():
        if any(re.search(rf"\b{re.escape(w)}", t) for w in words):
            return cat
    return "informatica"


def clean_query(text: str) -> str:
    t = _fold(text)
    t = re.sub(r"\b(quiero|busco|necesito|comprar(me)?|un|una|unos|unas|el|la|para|me|mi)\b", " ", t)
    words = list(dict.fromkeys(re.findall(r"[a-z0-9]+", t)))[:4]
    return " ".join(words) or "producto"


class DemoAI:
    name = "demo"

    def json(self, system: str, messages: list[dict], task: str) -> dict:
        if task == "evaluate":
            return self._evaluate(messages[-1]["content"])
        return self._interview(messages)

    def _interview(self, messages: list[dict]) -> dict:
        first = messages[0]["content"]
        answers = [m["content"] for m in messages[1:] if m["role"] == "user"
                   and not m["content"].startswith("(Mensaje del sistema")]
        asked = sum(1 for m in messages if m["role"] == "assistant")
        last = (answers[-1] if answers else "")
        draft = [{"label": "Producto", "value": first[:80]}]
        real_answers = [a for a in answers if not _fold(a).startswith("no se")]
        for q, a in zip(QUESTIONS, real_answers):
            draft.append({"label": q["label"], "value": a[:80]})
        if _fold(last).startswith("no se") and asked:
            q = QUESTIONS[min(len(real_answers), len(QUESTIONS) - 1)]
            return {"type": "question", "explanation": EXPLAIN, "draft": draft,
                    **{k: q[k] for k in ("question", "help", "options")}, "multi": q.get("multi", False)}
        if len(real_answers) >= len(QUESTIONS) or messages[-1]["content"].startswith("(Mensaje del sistema"):
            return {"type": "profile", "draft": draft, "profile": self._profile(first, real_answers)}
        q = QUESTIONS[len(real_answers)]
        return {"type": "question", "explanation": "", "draft": draft,
                **{k: q[k] for k in ("question", "help", "options")}, "multi": q.get("multi", False)}

    @staticmethod
    def _profile(first: str, answers: list[str]) -> dict:
        query = clean_query(first)
        budget = answers[3] if len(answers) > 3 else ""
        level = ("economico" if "barato" in _fold(budget) else "alto" if "pago mas" in _fold(budget)
                 else "medio" if budget else "sin_definir")
        nums = re.findall(r"\d[\d.]*", budget.replace(",", ""))
        max_ars = float(nums[0].replace(".", "")) if nums else None
        return {
            "product": query.capitalize(), "category": guess_category(first),
            "summary": f"(Modo demo) Buscamos {query} para: {', '.join(answers[:3]) or 'uso general'}.",
            "must": [{"feature": "Que sea el producto buscado", "what_is": "Filtramos accesorios y repuestos.",
                      "why": "En modo demo no hay análisis: con la IA, acá van las características ideales."}],
            "nice": [{"feature": "Marca conocida", "what_is": "Marcas con reseñas y service en Argentina.",
                      "why": "Suelen durar más y tener garantía local."}],
            "avoid_paying_for": [{"feature": "Funciones de marketing", "why": "En modo demo no se analizan."}],
            "budget": {"level": level, "max_ars": max_ars, "note": budget},
            "search_queries": [query], "suggested_models": [], "exclude_words": [],
        }

    @staticmethod
    def _evaluate(content: str) -> dict:
        lines = content.split("PUBLICACIONES\n", 1)[-1].splitlines()
        items = []
        for line in lines:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 4 or not parts[0].isdigit():
                continue
            words = parts[1].split()
            items.append({"id": int(parts[0]), "model": " ".join(words[:6]), "score": 70,
                          "meets": ["Coincide con lo que buscaste"], "doubts": ["Sin análisis (modo demo)"],
                          "reason": "Modo demo: evaluado por palabras, sin IA."})
        return {"items": items}


def dumps(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False)
