# Instrucciones para Claude Code en este repo

Compra Óptima: asesor de compras con IA para Argentina (entrevista → perfil → precios reales → seguir
precio). **Antes de tocar nada leé [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** y el documento del área
que vayas a cambiar ([`docs/README.md`](docs/README.md) tiene el índice). Cómo se trabaja: [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Reglas que siempre aplican

1. **Documentá todo, en el mismo commit.** Es un pedido explícito del dueño: este tiene que ser un trabajo de
   calidad de ingeniería. Cada cambio de comportamiento, prompt, tienda, límite, variable o decisión
   actualiza el documento que corresponde (tabla en `docs/README.md` y en `CONTRIBUTING.md`). Si un número
   cambia, corregilo en **todos** los lugares (`grep`). Los datos medidos llevan fecha. Una decisión de
   diseño nueva es un ADR en `docs/DECISIONS.md`.
2. **Medí antes de optimizar, y decí qué mediste.** Para IA: tokens reales por tarea
   (`usage.ledger.summary()` / `GET /api/usage`). No afirmes mejoras que no mediste; los límites o
   comportamientos de servicios externos se verifican, no se recuerdan de memoria (los nombres de modelo de
   Groq/OpenRouter, por ejemplo, cambian: ya hubo varios que no existían).
3. **Tests sin red, todos en verde antes de subir** (`.venv\Scripts\python.exe -m pytest -q`). Un bug real
   agrega un test con el caso real. `main` se publica solo en Render.
4. **La IA propone, el código dispone.** Validá y limitá en código todo lo que devuelva una IA.
5. **No se inventan datos:** lo no confirmado se marca "sin confirmar".
6. **No se evade ningún control anti-bot ni se leen sitios que lo prohíben.** Mercado Libre: sólo fichas
   públicas de ComparaYa. No rotar claves/proyectos para esquivar límites de una IA.
7. **Secretos:** nunca en el repo ni en mensajes; el repo es público. Pedile al dueño que no pegue claves en el
   chat (se cargan en `.env` y en Render).
8. **No toques el tracker de monitores** (`C:\Proyectos\monitor-precios-tracker`): es otro proyecto en
   producción. Mirá sus commits nuevos antes de empezar una tanda de trabajo, por si hay algo reutilizable.
9. **Texto para la persona:** español rioplatense (vos), claro, sin tecnicismos sin explicar. Informá con
   honestidad lo que no pudiste verificar o probar.

## Trampas conocidas de este entorno (Windows + Git Bash)

- Los scripts de Python pasados por *heredoc* **se comen las barras invertidas** (`\n`, `\d`, `\b` en
  regex quedan rotos y a veces generan caracteres de control). Para editar código con barras invertidas usá la
  herramienta de edición de archivos, o `chr(92)` en el script. Después de editar una regex, verificala con
  `cat -A`.
- Para esperar a que Render publique no uses `sleep` encadenados: un bucle `until` con `curl` contra
  `/api/status` o contra un archivo estático que contenga lo nuevo.
- `sqlite://` en memoria necesita `StaticPool` y `check_same_thread=False` para los tests con hilos.

## Dónde está cada cosa

`app/` (FastAPI + interfaz) · `core/` (lógica; el mapa está en `docs/ARCHITECTURE.md §3`) · `config/`
(`stores.json`, `ai_limits.json`) · `tests/` · `docs/` · `.github/workflows/` (`watch.yml` escaneo cada 3 h,
`tests.yml`) · `render.yaml` (despliegue).
