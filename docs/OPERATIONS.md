# Operación: configuración, despliegue, monitoreo y qué hacer cuando algo falla

- [1. Variables de entorno](#1-variables-de-entorno)
- [2. Desarrollo local](#2-desarrollo-local)
- [3. Despliegue](#3-despliegue)
- [4. Protección de la versión publicada](#4-protección-de-la-versión-publicada)
- [5. Monitoreo](#5-monitoreo)
- [6. Cuando algo falla (runbook)](#6-cuando-algo-falla-runbook)
- [7. Seguridad y datos](#7-seguridad-y-datos)

## 1. Variables de entorno

Local: archivo `.env` (no va al repo; plantilla en `.env.example`). Render: *Environment* del servicio.
Escaneo: *Secrets* del repo (sólo `DATABASE_URL` y `COMPRA_BOT_TOKEN`).

| Variable | Qué hace | Obligatoria | Dónde |
|---|---|---|---|
| `GEMINI_API_KEY` | Clave de Google AI Studio | una IA al menos | Render, local |
| `GEMINI_MODEL` | Modelos de Gemini en orden, separados por coma | no | ídem |
| `GROQ_API_KEY`, `GROQ_MODELS` | Groq y su lista de modelos | no | ídem |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODELS` | OpenRouter y su lista | no | ídem |
| `CEREBRAS_API_KEY`, `CEREBRAS_MODELS` | Cerebras (fuera de la cadena por defecto: ya no es gratis) | no | ídem |
| `AI_PROVIDER` | `demo` fuerza el modo sin IA (cualquier otro valor se ignora) | no | local |
| `AI_CHAIN` | IA que se consideran y orden general (por defecto `gemini,groq,openrouter`) | no | ídem |
| `AI_ROUTE_INTERVIEW` / `_PROFILE` / `_EVALUATE` | Orden de IA por tarea ([AI.md §3](AI.md#3-reparto-de-tareas-entre-ia)) | no | ídem |
| `ACCESS_CODE` | Código que pide la web antes de usar la IA; vacío = no pide | en lo publicado | Render |
| `LIMIT_INTERVIEW_PER_HOUR` / `LIMIT_SEARCH_PER_HOUR` | Límite por persona (IP) y hora (por defecto 80 y 12) | no | Render |
| `DATABASE_URL` | Postgres de Neon; sin ella, SQLite local `data/compra.db` | para Seguir precio | Render, Actions |
| `COMPRA_BOT_TOKEN` | Token del bot de Compra Óptima (@BotFather) | para Seguir precio | Render, Actions |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Dónde llegan los "¿Te sirvió?" al dueño (sirve el bot del tracker) | no | Render |
| `RENDER`, `RENDER_EXTERNAL_URL`, `PORT` | Las pone Render solas | — | Render |
| `PUBLIC_URL` | Dirección pública para registrar el webhook de Telegram fuera de Render (si no hay `RENDER_EXTERNAL_URL`) | no | otro hosting |
| `GITHUB_ACTIONS` | La pone GitHub solo (activa `cloud_blocked`) | — | Actions |

Constantes de comportamiento (no variables): `core/settings.py` (`MIN_QUESTIONS`, `MAX_QUESTIONS`,
`MAX_LISTINGS_TO_EVALUATE`, `SEARCH_TIMEOUT_S`) y `config/ai_limits.json` (límites de cada IA).

## 2. Desarrollo local

```powershell
.\iniciar.ps1                              # crea el entorno, copia .env.example, abre http://127.0.0.1:8010
.venv\Scripts\python.exe -m pytest -q      # tests (sin red)
.venv\Scripts\python.exe -m core.watch     # corre el escaneo una vez (usa DATABASE_URL o SQLite local)
```

Sin claves arranca en **modo demo**. Sin `DATABASE_URL`, "Seguir precio" funciona contra SQLite local pero
necesita `COMPRA_BOT_TOKEN`.

## 3. Despliegue

**Web (Render).** `render.yaml` define el servicio (`pip install -r requirements.txt`,
`uvicorn app.main:app`, health check `/api/status`, `autoDeploy: true`). Cada push a `main` se publica
solo, en ~1,5 minutos. Las variables que no van al repo están con `sync: false` y se cargan a mano en el
panel. Alta inicial: *New → Blueprint* → repo `compra-optima`.

**Base (Neon).** Proyecto Postgres; la *connection string* (`postgresql://…sslmode=require`) va en
`DATABASE_URL` de Render y como secret del repo. El esquema se crea solo al arrancar. `core/db.py` acepta
`postgres://` y `postgresql://` y fija el driver `psycopg`.

**Escaneo (GitHub Actions).** `watch.yml`: cron `23 */3 * * *` y botón *Run workflow*. Si faltan los
secrets, lo informa y termina sin error. `tests.yml`: `pytest` en cada push.

**Mantener despierta la web (`keepalive.yml`).** Render gratis duerme el servicio tras 15 minutos sin
visitas y despertarlo tarda hasta ~1 minuto. Cada 10 minutos un workflow hace `GET /api/status` (una ruta
real de la app: Render contesta solo algunas, como `robots.txt`, sin despertar nada). Costo y límites:
- Un servicio despierto 24/7 gasta ~744 de las **750 horas gratis del mes, compartidas por todos los servicios
  gratis de la cuenta** de Render: con otro servicio gratis, se agotarían y Render suspende los servicios
  hasta el mes siguiente.
- Es de mejor esfuerzo: GitHub a veces demora el cron y puede quedar un hueco de más de 15 minutos (un
  arranque en frío ocasional).
- Para apagarlo, borrar `.github/workflows/keepalive.yml`. No verificamos los términos de Render sobre
  pings de este tipo (documentación consultada el 26/11/2026 sólo describe el apagado a los 15 minutos y las
  750 horas): revisarlos antes de depender de esto.

**Bot (Telegram).** @BotFather → `/newbot`. El token va en `COMPRA_BOT_TOKEN` (Render y secret). Al
arrancar, la web registra el webhook en `RENDER_EXTERNAL_URL`. No usar el bot del tracker.

**Verificar un despliegue:** `GET https://<servicio>.onrender.com/api/status` devuelve `provider`
(las IA activas, ej. `gemini + groq + openrouter`), `follow_enabled` y `code_required`.

## 4. Protección de la versión publicada

`core/guard.py`:
- Con `ACCESS_CODE`, la web muestra "Prueba privada" y pide el código; el navegador lo recuerda y lo manda
  en la cabecera `X-Access-Code`. Sin código, los endpoints de IA responden 401.
- Límite por persona (IP, tomada de `X-Forwarded-For` detrás del proxy de Render) y por hora: 80 turnos de
  entrevista, 12 búsquedas, 30 seguimientos, 40 consultas de Mercado Libre, 10 comentarios.
- Los límites sólo aplican con `ACCESS_CODE` definido (en tu PC no estorban).
- Objetivo: que nadie de afuera gaste el cupo gratis de las IA.

## 5. Monitoreo

- **`GET /api/usage`** (pide el código si está configurado): tokens promedio por tarea y modelo, cuota
  usada contra el límite gratis de cada uno y cantidad de consultas fallidas. Es la herramienta para
  decidir si hay que cambiar rutas, modelos o pasar a un plan pago.
- **Logs de Render:** cada consulta de IA que falla y pasa a la siguiente deja una línea (`ai`), igual que
  las tiendas caídas (`search`), las evaluaciones incompletas (`candidates`) y los comentarios (`feedback`).
- **Pestaña Actions** del repo: estado del escaneo y de los tests.
- **"Dónde buscamos"** (al final de los resultados): qué tiendas respondieron y cuáles dieron error.

## 6. Cuando algo falla (runbook)

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| "Llegamos al límite gratis de la IA" | cupo de Gemini agotado (por minuto o por día) | `/api/usage` para ver cuál; esperar (el diario se reinicia ~5 AM Argentina); verificar que Groq/OpenRouter tengan clave; evaluar facturación |
| "La IA de Google está saturada" | 503 de Gemini (frecuente en el plan gratis) | la cadena pasa a Groq sola; si persiste, mirar `usage.health` en `/api/usage` |
| La búsqueda tarda más de 2 min | Gemini inestable o tandas que fallan y se reintentan | normal con 503 repetidos; ver `/api/usage` (consultas fallidas y duración) |
| "Seguir precio" aparece gris | falta `COMPRA_BOT_TOKEN` o base (`follow_enabled: false` en `/api/status`) | cargar las variables en Render |
| El bot no responde al `/start` | webhook no registrado | mirar logs de arranque (`No se pudo registrar el webhook`); verificar `RENDER_EXTERNAL_URL` y el token; redeploy |
| Llegan avisos que no son bajas | publicación distinta del mismo modelo | ver [FOLLOW.md §3](FOLLOW.md#3-qué-es-bajar-de-verdad) (primer escaneo fija la partida) |
| Una tienda siempre da 403 | bloquea servidores en la nube | marcarla `cloud_blocked` en `config/stores.json` |
| "Ver en Frávega" abre una página vacía | link armado mal | ver [STORES.md §3](STORES.md#3-particularidades-de-frávega) |
| Falta Mercado Libre | ComparaYa no tiene la ficha | `comparaya_slugs` guarda "no existe" 7 días; subir `SLUG_VERSION` si se mejoró cómo se arman direcciones |
| El escaneo no corre | repo inactivo 60 días (GitHub lo desactiva) o faltan secrets | reactivar en Actions; revisar secrets |
| Render tarda ~1 min en abrir | duerme tras 15 min sin uso; `keepalive.yml` lo evita pero es de mejor esfuerzo | esperar; mirar la pestaña Actions por si `mantener-despierto` falla o está desactivado (repos sin actividad por 60 días) |
| Un modelo de Groq/OpenRouter da 404 | el servicio retiró el modelo | listar los reales (`/v1/models`) y cambiar `GROQ_MODELS` / `OPENROUTER_MODELS` |

## 7. Seguridad y datos

- Las claves **nunca** van al repo (`.env` está en `.gitignore`). Si una clave se pega en un chat o se
  filtra, se rota en el servicio y se actualiza en Render y en los secrets.
- El repo es **público**: no debe contener claves, `.env` ni la base.
- Datos personales guardados: `chat_id` de Telegram y el nombre que Telegram informa, sólo para los
  avisos. No hay emails, cuentas ni contraseñas. Los comentarios de "¿Te sirvió?" no se guardan.
- Lo que se manda a las IA es texto de compras (la conversación y títulos de publicaciones). Los planes
  gratis de las IA pueden usar esos datos para mejorar sus modelos: hay que decírselo a quien lo use.
- El conteo de límites por persona es en memoria: un reinicio lo borra (aceptable para esta escala).
