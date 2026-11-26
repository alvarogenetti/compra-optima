# Arquitectura de Compra Óptima

Asesor de compras para Argentina: una IA entrevista a la persona sobre **cómo va a usar** lo que quiere
comprar, arma un perfil explicado, busca precios **reales** en tiendas argentinas, recomienda en tres
rangos y puede seguir el precio y avisar por Telegram cuando baja de verdad.

Este documento es el mapa. El detalle de cada parte está en [`docs/`](README.md).

- [1. Vista general](#1-vista-general)
- [2. Ciclo de vida de una consulta](#2-ciclo-de-vida-de-una-consulta)
- [3. Mapa de módulos](#3-mapa-de-módulos)
- [4. Modelo de datos](#4-modelo-de-datos)
- [5. Despliegue](#5-despliegue)
- [6. Configuración](#6-configuración)
- [7. Principios de diseño](#7-principios-de-diseño)
- [8. Cómo falla (y qué pasa cuando falla)](#8-cómo-falla-y-qué-pasa-cuando-falla)

## 1. Vista general

```
                    Navegador (app/static: index.html, app.js, styles.css)
                    guarda la conversación y el perfil; el servidor no guarda estado de sesión
                                        │  JSON
                                        ▼
        ┌─────────────────────  FastAPI (app/main.py)  ──────────────────────┐
        │  /api/interview   /api/candidates[/id]   /api/ml   /api/follow      │
        │  /api/feedback    /api/usage        /api/telegram/<clave>           │
        │  guard.py: código de acceso + límite por persona                    │
        └───────┬──────────────┬──────────────────┬──────────────┬────────────┘
                │              │                  │              │
         interview.py     candidates.py       comparaya.py    follow.py
         (preguntas,      (búsqueda,          (Mercado Libre  (Seguir precio,
          perfil)          evaluación,         vía fichas      bot de Telegram)
                │          rangos)             públicas)            │
                │              │                                    │
                ▼              ▼                                    ▼
            ai.py  ◄──── usage.py (cuota)          search.py (VTEX, WooCommerce, Tiendanube)
   Gemini · Groq · OpenRouter                       │  stores.json: 21 tiendas por categoría
                                                    ▼
                                         tiendas argentinas (HTTP público)

        GitHub Actions cada 3 h ──► watch.py ──► Neon (Postgres) ◄── la web (Render)
                                          └──► Telegram (avisos)
```

Dos procesos separados que comparten una base:

- **La web** (Render): atiende a las personas. Stateless salvo caches en memoria.
- **El escaneo** (GitHub Actions, `core/watch.py`): corre cada 3 horas, busca los productos que alguien
  sigue, guarda el historial y avisa. **No usa IA.**

## 2. Ciclo de vida de una consulta

1. **Entrevista.** `POST /api/interview` con la conversación completa. `interview.step()` decide el
   prompt (corto o completo) y la tarea (`interview` o `profile`), `ai.py` elige IA, y la respuesta se
   valida (`normalize`). Se repite hasta que la IA devuelve `type: "profile"` (mínimo 3 preguntas, máximo
   10).
2. **Búsqueda.** `POST /api/candidates` con el perfil y el código postal arranca la búsqueda **en segundo
   plano** (`core/jobs.py`) y contesta enseguida con un `job_id`; la página pregunta cada 1,5 s a
   `GET /api/candidates/<id>` y muestra el paso actual (`candidates.STAGES`: tiendas → Mercado Libre y otras →
   evaluación, con "tanda 2 de 5" → disponibilidad → armado) y el tiempo, hasta que llega el resultado o el
   error. Qué hace `candidates.find`:
   1. Tiendas de la categoría (`stores.for_categories`), salteando las `cloud_blocked` si se corre en la
      nube.
   2. `search_all`: búsquedas amplias + por especificación dura + modelos sugeridos, en paralelo (16 hilos),
      con filtro de precio y 2 páginas en VTEX para las de especificación.
   3. `comparaya_listings`: ofertas de Mercado Libre y de tiendas que no leemos, para los 5 primeros
      modelos sugeridos.
   4. `prefilter` (sin IA): dedupe, presupuesto, relevancia, reparto por precio → hasta 150 publicaciones.
   5. `evaluate`: caché → tandas de 30 en paralelo → reintento de lo incompleto → `add_siblings`.
   6. `drop_unavailable`: simulación de compra de VTEX con el código postal.
   7. `group_by_model` → `build_tiers` (3 rangos por precio) → `recommend` → `near_misses`.
   8. `upgrades.add_costs` y `confirm_upgrades`: cuánto cuesta completar un "casi cumple".
3. **Mercado Libre bajo demanda.** `POST /api/ml` (botón "¿Y en Mercado Libre?"): ficha de ComparaYa.
4. **Seguir precio.** `POST /api/follow` crea el seguimiento y devuelve el link de Telegram; el
   `/start <token>` llega por webhook a `/api/telegram/<clave>` y lo vincula al chat.
5. **Escaneo** (cada 3 h): para cada modelo seguido, busca, guarda el precio, calcula el descuento real
   contra el precio habitual y avisa.

## 3. Mapa de módulos

| Módulo | Responsabilidad | Documento |
|---|---|---|
| `app/main.py` | Endpoints, protección, arranque (webhook de Telegram) | este |
| `app/static/*` | Interfaz: 5 vistas (código, inicio, entrevista, perfil, resultados) sin frameworks | este |
| `core/interview.py` | Prompts de la entrevista, normalización de preguntas y perfil | [AI.md](AI.md), [SEARCH.md](SEARCH.md) |
| `core/ai.py` | Proveedores de IA, cadena, rutas, pausas | [AI.md](AI.md) |
| `core/usage.py` | Registro de tokens, cuotas, salud | [AI.md](AI.md) |
| `core/demo.py` | IA falsa para probar sin clave | [AI.md](AI.md) |
| `core/stores.py` | Registro de tiendas (`config/stores.json`) | [STORES.md](STORES.md) |
| `core/search.py` | Lectores de VTEX/WooCommerce/Tiendanube, disponibilidad, especificaciones de ficha | [STORES.md](STORES.md) |
| `core/comparaya.py` | Mercado Libre vía fichas públicas de ComparaYa | [STORES.md](STORES.md) |
| `core/candidates.py` | Prefiltro, evaluación, caché, rangos, recomendación | [SEARCH.md](SEARCH.md) |
| `core/jobs.py` | Búsqueda en segundo plano con progreso (hasta 3 a la vez) | este (§2) y [SEARCH.md](SEARCH.md) |
| `core/upgrades.py` | Precio de módulos de RAM y SSD para completar una notebook | [SEARCH.md](SEARCH.md) |
| `core/specs.py` | Ficha del fabricante con Gemini + Google | [SEARCH.md](SEARCH.md) |
| `core/follow.py`, `core/telegram.py` | Seguir precio y bot | [FOLLOW.md](FOLLOW.md) |
| `core/watch.py`, `core/market.py` | Escaneo programado y descuento real | [FOLLOW.md](FOLLOW.md) |
| `core/db.py` | Esquema (SQLAlchemy Core) y conexión | este |
| `core/guard.py` | Código de acceso y límite por persona | [OPERATIONS.md](OPERATIONS.md) |
| `core/feedback.py` | "¿Te sirvió?" por Telegram | [OPERATIONS.md](OPERATIONS.md) |
| `core/settings.py` | Variables de entorno y constantes | [OPERATIONS.md](OPERATIONS.md) |
| `core/price_parser.py` | Precios en pesos argentinos ("$ 1.234,56") | (del tracker) |

## 4. Modelo de datos

Postgres en Neon (`DATABASE_URL`); sin esa variable, SQLite en `data/compra.db` (sólo desarrollo). El
esquema está en `core/db.py` y se crea solo al arrancar (`meta.create_all`).

| Tabla | Para qué | Quién escribe |
|---|---|---|
| `watchers` | Una fila por persona (chat de Telegram). Sin nombre real obligatorio ni email | `follow.link` |
| `follows` | "Seguir precio": modelo, categoría, precio de partida, estado de avisos. `token` único para el link de Telegram; `watcher_id` vacío hasta que la persona toca Iniciar | web, bot, `watch` |
| `prices` | Historial: mejor precio por tienda en cada escaneo, por modelo (compartido por todos los que lo siguen) | `watch` |
| `comparaya_slugs` | Dirección de la ficha de ComparaYa de cada modelo (o "no existe"), con fecha | `comparaya` |
| `ai_usage` | Una fila por consulta a la IA: tokens reales, tarea, modelo, estado | `usage` |
| `model_specs` | Ficha del fabricante por modelo, con fuentes | `specs` |

Datos personales: sólo el `chat_id` de Telegram y el nombre que Telegram da. No hay cuentas ni
contraseñas. Los comentarios de "¿Te sirvió?" no se guardan: llegan por Telegram al dueño.

Caches y estado **en memoria** (se pierden al reiniciar, son recomputables): evaluaciones
(`candidates._eval_cache`), pausas de modelos de IA (`ai._cooldown`), fichas ya vistas (`specs._memory`) y las
búsquedas en curso o recién terminadas (`jobs.manager`, 30 minutos).

**Estado en el navegador** (`app/static/app.js`, `localStorage`): el servidor no guarda nada de la sesión.
- La página **siempre abre en el inicio**; lo que se está haciendo vive en memoria.
- **Historial de búsquedas** (`compra-optima:history:v1`, hasta 8): cada perfil y cada resultado se guardan
  (perfil, resultados, conversación, fecha). Se reabren desde "Tus búsquedas anteriores" en el inicio, con la
  fecha de la búsqueda y un aviso de que los precios son los de ese momento (con botón "Actualizar precios").
  Un perfil corregido es una entrada nueva: la anterior conserva sus resultados. Si el almacenamiento se
  llena, se descartan las más viejas.
- Otras claves: `compra-optima:code` (código de acceso) y `compra-optima:cp` (código postal).
- Límite: el historial es **por navegador y por dispositivo** (no hay cuentas). Borrar los datos del sitio lo
  borra. Ver [DECISIONS.md](DECISIONS.md) ADR-015.

## 5. Despliegue

| Pieza | Dónde | Cómo se actualiza |
|---|---|---|
| Web | Render (plan gratis), `render.yaml` | cada push a `main` se publica solo (~1,5 min) |
| Base | Neon (Postgres gratis), región sa-east-1 | — |
| Escaneo | GitHub Actions `watch.yml`, cron `23 */3 * * *` | con el repo |
| Mantener despierta la web | GitHub Actions `keepalive.yml`, cron `*/10 * * * *` (ADR-016) | con el repo |
| Tests | GitHub Actions `tests.yml` en cada push | con el repo |
| Bot | Telegram (@BotFather), webhook registrado al arrancar en Render | — |

Detalle, variables y secrets en [OPERATIONS.md](OPERATIONS.md).

## 6. Configuración

Dos clases, separadas a propósito:

- **Secretos y entorno** (`.env`, Render, GitHub Secrets): claves, códigos, URL de base. Nunca en el repo.
- **Datos que cambian sin tocar código** (`config/`): `stores.json` (tiendas y categorías) y
  `ai_limits.json` (límites de cada IA). Se editan, se prueban y se commitean.

## 7. Principios de diseño

1. **La IA propone, el código dispone.** Todo lo que devuelve una IA se valida, recorta y limita en código
   antes de mostrarse (tope de puntaje, categoría válida, ids existentes). Nada de lo que dice la IA se
   confía como hecho: los modelos que sugiere sólo se recomiendan si **aparecen en una tienda**.
2. **Datos reales o "sin confirmar".** Precios y stock salen de las tiendas; lo que no se puede confirmar
   (ficha del fabricante, si se puede ampliar la RAM) se marca así y no se inventa.
3. **Degradar, no romper.** Sin clave de IA: modo demo. Sin Telegram: "Seguir precio" apagado. Sin
   ComparaYa: se muestra lo demás. Una tienda caída se informa en "Dónde buscamos" y se sigue.
4. **No se evade ningún control anti-bot ni se infringen términos de servicio.** Mercado Libre no se lee
   directo ni por intermediarios que lo scrapean; sólo vía fichas que un comparador publica y permite leer
   (ver [DECISIONS.md](DECISIONS.md) ADR-005).
5. **Sin estado de sesión en el servidor.** La conversación y el perfil viajan en cada pedido. Simplifica
   el despliegue y la escala; cuesta tokens (ver [AI.md](AI.md) §5).
6. **Medir antes de optimizar.** El consumo de IA se registra por consulta (`/api/usage`).
7. **Tests sin red.** IA y tiendas se reemplazan por dobles; corren en cada push.
8. **Documentación en el mismo commit que el cambio.** Ver [`CLAUDE.md`](../CLAUDE.md) y
   [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## 8. Cómo falla (y qué pasa cuando falla)

| Falla | Efecto | Defensa |
|---|---|---|
| Gemini 429 (cuota) | esa IA/modelo no responde | se pausa el tiempo indicado; el pedido pasa al siguiente modelo y luego a otra IA |
| Gemini 503 / timeout | respuestas lentas o ausentes | reintento corto, siguiente modelo, salud baja → pasa al final del orden |
| Todas las IA sin cuota | la búsqueda o la pregunta no se puede responder | mensaje claro con en cuánto reintentar; la caché de evaluaciones evita repetir lo ya hecho |
| Servidor reiniciado a mitad de una búsqueda | el trabajo en segundo plano desaparece | el sondeo recibe 404 y la página lo dice ("Esa búsqueda ya no está… probá de nuevo"); la caché de evaluaciones se perdió, así que repite el trabajo |
| Demasiadas búsquedas a la vez (más de 3) | cada una gasta cuota de IA y consulta decenas de tiendas | el `POST` responde 429 "Hay varias búsquedas en curso…" |
| Respuesta de IA incompleta o inválida | se perderían candidatos | `product:false` explícito + reintento de lo faltante; JSON rescatado de texto sucio |
| Tienda bloquea (403) | menos resultados | se informa en "Dónde buscamos"; las `cloud_blocked` ni se consultan desde la nube |
| Publicación sin stock en la zona | recomendar algo que no se puede comprar | simulación de compra de VTEX con el código postal |
| ComparaYa caído o sin ficha | sin precio de Mercado Libre | link de búsqueda en ML y "No encontramos este modelo" |
| Base caída | sin "Seguir precio" ni historial | el resto de la web sigue; usage y caches degradan a memoria |
| Render dormido (plan gratis) | primera visita tarda ~1 min | documentado; sin solución en el plan gratis |
| Servidor reiniciado | se pierden caches en memoria | se reconstruye lo gastado hoy desde `ai_usage` |
