# Cómo trabajar en Compra Óptima

Este proyecto se mantiene con calidad de ingeniería: cada cambio entra probado, medido cuando corresponde, y
**con su documentación en el mismo commit**. Esta guía dice cómo.

## Puesta en marcha

```powershell
git clone https://github.com/alvarogenetti/compra-optima
cd compra-optima
.\iniciar.ps1                              # entorno virtual, dependencias, .env, y abre http://127.0.0.1:8010
.venv\Scripts\python.exe -m pytest -q      # tests: tienen que pasar todos
```

Sin claves de IA arranca en modo demo. Variables y servicios: [docs/OPERATIONS.md](docs/OPERATIONS.md).
Para entender el sistema: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Definición de "terminado"

Un cambio está terminado cuando:

1. **Funciona de verdad.** Se probó con datos reales (tiendas, IA) y no sólo con dobles. Si cambia la
   interfaz, se miró en el navegador, en pantalla ancha y en celular.
2. **Tiene tests** que fallan sin el cambio y pasan con él (`pytest`, sin red).
3. **Se midió, si toca IA.** Prompt, ruta, modelo o tamaño de pedido: tokens por tarea antes y después
   (`GET /api/usage` o `usage.ledger.summary()`) y calidad de la salida con los casos que ya fallaron
   ([docs/AI.md §8](docs/AI.md#8-reglas-para-tocar-un-prompt)).
4. **Está documentado** en el mismo commit (tabla de abajo).
5. **No rompe lo que no tocaba:** toda la suite pasa y `git status` no trae archivos de más (claves, `.env`,
   `data/`).

## Documentación: qué se actualiza con cada cambio

| Si cambiaste… | Actualizá… |
|---|---|
| un prompt, una ruta, un modelo o un límite de IA | `docs/AI.md` (y `config/ai_limits.json`) |
| cómo se busca, filtra, evalúa o agrupa | `docs/SEARCH.md` |
| una tienda, una plataforma o Mercado Libre | `docs/STORES.md` |
| Seguir precio, avisos o el escaneo | `docs/FOLLOW.md` |
| una variable, un secret, el despliegue o algo operativo | `docs/OPERATIONS.md` y `.env.example` |
| algo que condiciona el diseño o revierte una decisión | `docs/DECISIONS.md` (ADR nuevo, o marcar el anterior como reemplazado) |
| un módulo, una tabla o un endpoint | `docs/ARCHITECTURE.md` |
| lo que ve o necesita saber quien usa la app | `README.md` |

Cuando un número cambia (una constante, un límite, un tiempo medido), se corrige **en todos los lugares
donde aparece**: buscalo con `grep` antes de dar el cambio por terminado. Los datos medidos llevan **fecha**.

## Convenciones de código

- Python 3.12, tipos en las firmas, `from __future__ import annotations`.
- **Docstrings y comentarios en español**, explicando el *porqué* (qué problema resuelve, qué se midió), no
  repitiendo el código. Texto que ve la persona: español rioplatense (vos), claro y sin tecnicismos sin
  explicar.
- Sin frameworks en el frontend (HTML, CSS y JS planos). Todo texto que viene de una tienda o de la IA se
  escapa antes de mostrarse (`esc()` en `app.js`).
- **La IA propone, el código dispone:** todo lo que devuelve una IA se valida y limita en código
  ([docs/ARCHITECTURE.md §7](docs/ARCHITECTURE.md#7-principios-de-diseño)).
- **Constantes con nombre** (`EVAL_BATCH`, `FAIL_CAP`...) y a la vista, no números mágicos; los datos que
  cambian sin tocar código van a `config/`.
- Errores que explican qué hacer (`AIError` con mensaje para la persona), no trazas.
- Nada de claves, tokens ni URLs de base en el código, los tests ni los commits.

## Tests

- **Sin red, siempre.** La IA se reemplaza con dobles (`set_ai(...)`, `FakeAI`), las tiendas con
  `httpx.MockTransport` y respuestas reales guardadas.
- `tests/conftest.py` da a cada test un registro de uso de IA, una caché de evaluaciones y una base en
  memoria **nuevos**: no dependas del estado de otro test ni escribas en `data/`.
- Un bug encontrado en producción o con datos reales **agrega un test** con el caso real (títulos, precios).
- Verificá que un test nuevo **falla sin el cambio** (si pasa igual, no prueba nada).

## Commits

- Mensaje: primera línea con *qué* cambia y para quién; cuerpo con el *por qué*, qué se midió o se
  descubrió, y qué queda pendiente. Ejemplos en `git log`.
- Terminan con la línea `Co-Authored-By:` que corresponda cuando trabaja una IA.
- Un commit = un cambio con sus tests y su documentación. No mezclar cambios no relacionados.
- `main` se publica solo en Render con cada push: **los tests tienen que pasar antes de subir**.

## Cosas que no se negocian

1. **No se evade ningún control anti-bot** y no se leen sitios que lo prohíben. Mercado Libre sólo vía
   fichas públicas de ComparaYa ([docs/STORES.md §5](docs/STORES.md#5-reglas-que-no-se-rompen)).
2. **No se inventan datos.** Lo que no se puede confirmar se marca "sin confirmar".
3. **Las claves no se pegan en chats ni en el repo.** Si una se filtra, se rota.
4. **El tracker de monitores no se toca** desde acá (es otro proyecto que corre en producción). Antes de
   trabajar en este repo conviene mirar los commits nuevos del tracker: hay mejoras reutilizables.

## Cómo hacer los cambios frecuentes

- **Sumar una tienda:** [docs/STORES.md §6](docs/STORES.md#6-cómo-sumar-una-tienda).
- **Cambiar un prompt:** [docs/AI.md §8](docs/AI.md#8-reglas-para-tocar-un-prompt).
- **Cambiar de IA o de modelo:** `GEMINI_MODEL` / `GROQ_MODELS` / `OPENROUTER_MODELS` y
  `config/ai_limits.json`; después medir con `/api/usage`.
- **Sumar un proveedor de IA:** una clase con el método `json(system, messages, task)` en `core/ai.py`,
  registrada en `configured_providers`, con su entrada en `config/ai_limits.json` y un test.
