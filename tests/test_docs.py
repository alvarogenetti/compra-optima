"""La documentación también se prueba: si el código cambia y el documento no, esto falla.

Qué verifica:
  - los enlaces entre documentos (y a sus secciones) existen;
  - cada módulo de core/ figura en docs/ARCHITECTURE.md;
  - cada variable de entorno que lee el código está explicada en docs/OPERATIONS.md;
  - los ADR de docs/DECISIONS.md están numerados en orden y sin saltos;
  - cada documento de docs/ está en el índice docs/README.md.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
MD_FILES = sorted(DOCS.glob("*.md")) + [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "CLAUDE.md"]


def _slug(heading: str) -> str:
    """Ancla como la genera GitHub: minúsculas, sin signos, espacios a guiones."""
    text = re.sub(r"`", "", heading.strip().lower())
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s", "-", text)


def _anchors(path: Path) -> set[str]:
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^#{1,6}\s+(.*)$", line)
        if m:
            out.add(_slug(m.group(1)))
    return out


def test_los_enlaces_entre_documentos_existen():
    rotos = []
    for md in MD_FILES:
        for target in re.findall(r"\]\(([^)\s]+)\)", md.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            file_part, _, anchor = target.partition("#")
            dest = (md.parent / file_part).resolve() if file_part else md
            if not dest.exists():
                rotos.append(f"{md.name}: {target} (no existe el archivo)")
            elif anchor and dest.suffix == ".md" and anchor not in _anchors(dest):
                rotos.append(f"{md.name}: {target} (no existe la sección)")
    assert not rotos, "enlaces rotos:\n" + "\n".join(rotos)


def test_cada_modulo_de_core_esta_en_la_arquitectura():
    arquitectura = (DOCS / "ARCHITECTURE.md").read_text(encoding="utf-8")
    faltan = [p.stem for p in sorted((ROOT / "core").glob("*.py"))
              if p.stem != "__init__" and f"`core/{p.stem}.py`" not in arquitectura
              and f"core/{p.stem}.py" not in arquitectura and p.stem not in arquitectura]
    assert not faltan, f"módulos sin documentar en docs/ARCHITECTURE.md: {faltan}"


def test_cada_variable_de_entorno_esta_en_operaciones():
    operaciones = (DOCS / "OPERATIONS.md").read_text(encoding="utf-8")
    leidas = set()
    for py in list((ROOT / "core").glob("*.py")) + [ROOT / "app" / "main.py"]:
        leidas |= set(re.findall(r"""getenv\(\s*["']([A-Z][A-Z0-9_]+)["']""", py.read_text(encoding="utf-8")))
    # variables armadas dinámicamente: AI_ROUTE_<TAREA> y <IA>_MODELS
    leidas |= {"AI_ROUTE_INTERVIEW", "GROQ_MODELS", "OPENROUTER_MODELS", "CEREBRAS_MODELS"}
    faltan = sorted(v for v in leidas if v not in operaciones)
    assert not faltan, f"variables de entorno sin explicar en docs/OPERATIONS.md: {faltan}"


def test_los_adr_estan_numerados_en_orden():
    numeros = [int(n) for n in re.findall(r"^### ADR-(\d+)", (DOCS / "DECISIONS.md").read_text(encoding="utf-8"), re.M)]
    assert numeros and numeros == list(range(1, len(numeros) + 1)), f"ADR fuera de orden o con saltos: {numeros}"


def test_todos_los_documentos_estan_en_el_indice():
    indice = (DOCS / "README.md").read_text(encoding="utf-8")
    faltan = [p.name for p in DOCS.glob("*.md") if p.name != "README.md" and p.name not in indice]
    assert not faltan, f"documentos que no figuran en docs/README.md: {faltan}"
