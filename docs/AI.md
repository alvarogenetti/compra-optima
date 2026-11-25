# La capa de IA: proveedores, reparto de tareas, presupuesto de tokens y prompts

Todo lo que toca una IA pasa por `core/ai.py` (`get_ai().json(system, messages, task)`). Este documento
explica **qué IA hay, cómo se reparte el trabajo, cómo se cuida la cuota, cuánto cuesta cada paso (medido)
y qué dice cada prompt y por qué**. Si cambiás algo de esto, actualizá este archivo en el mismo commit.

- [1. Visión general](#1-visión-general)
- [2. Proveedores y modelos](#2-proveedores-y-modelos)
- [3. Reparto de tareas entre IA](#3-reparto-de-tareas-entre-ia)
- [4. Control de cuota y registro de uso](#4-control-de-cuota-y-registro-de-uso)
- [5. Presupuesto de tokens (medido)](#5-presupuesto-de-tokens-medido)
- [6. Técnicas de ahorro](#6-técnicas-de-ahorro)
- [7. Catálogo de prompts](#7-catálogo-de-prompts)
- [8. Reglas para tocar un prompt](#8-reglas-para-tocar-un-prompt)
- [9. Límites conocidos](#9-límites-conocidos)

## 1. Visión general

La app usa IA para tres cosas, todas devuelven **JSON** y ninguna escribe nada en la base:

| Tarea (`task`) | Qué hace | Tamaño del pedido | Dónde está el prompt |
|---|---|---|---|
| `interview` | Una pregunta de la entrevista (prompt **corto**: sólo preguntas) | chico: ~1.200 a 1.700 tokens de entrada, ~300 a 500 de salida | `core/interview.py` (`SYSTEM_QUESTIONS`) |
| `profile` | El turno que **puede cerrar** la entrevista con el perfil (prompt **completo**) | mediano: ~3.000 a 4.500 de entrada, ~1.000 a 2.200 de salida | `core/interview.py` (`SYSTEM_QUESTIONS` + `SYSTEM_PROFILE`) |
| `evaluate` | Evalúa una tanda de hasta 30 publicaciones contra el perfil | grande: ~3.300 a 4.700 de entrada, 2.000 a 6.000 de salida | `core/candidates.py` (`EVAL_SYSTEM`) |

Además, `core/specs.py` consulta la ficha del fabricante con Gemini + búsqueda de Google (hoy apagado en
el plan gratis, ver [§9](#9-límites-conocidos)).

La conversación **no se guarda en el servidor**: el navegador la manda completa en cada pedido
(`/api/interview`). Por eso el costo de la entrevista crece con la cantidad de turnos.

## 2. Proveedores y modelos

| Proveedor | Clase | Clave | Modelos por defecto (en orden) |
|---|---|---|---|
| Gemini (Google AI Studio) | `GeminiAI` | `GEMINI_API_KEY` | `gemini-3.1-flash-lite`, `gemini-3.5-flash-lite`, `gemini-flash-lite-latest`, `gemini-3-flash-preview`, `gemini-3.5-flash`, `gemini-flash-latest` (`GEMINI_MODEL`) |
| Groq | `OpenAICompatAI` | `GROQ_API_KEY` | `openai/gpt-oss-120b`, `openai/gpt-oss-20b` (`GROQ_MODELS`) |
| OpenRouter | `OpenAICompatAI` | `OPENROUTER_API_KEY` | `nvidia/nemotron-3.5-lightning:free`, `nvidia/nemotron-3-ultra-550b-a55b:free`, `google/gemma-4-31b-it:free` (`OPENROUTER_MODELS`) |
| Cerebras | `OpenAICompatAI` | `CEREBRAS_API_KEY` | soportada pero **fuera de la cadena**: desde julio de 2026 no tiene plan gratis |
| Demo | `DemoAI` (`core/demo.py`) | (ninguna) | sin IA: preguntas fijas y evaluación por palabras, para probar la interfaz |

Una IA entra en la cadena sólo si tiene clave. Con `AI_PROVIDER=demo` se fuerza el modo demo.
Los nombres de modelo cambian seguido: la lista real de cada servicio está en
`https://api.groq.com/openai/v1/models` y `https://openrouter.ai/api/v1/models` (con la clave).

Calidad observada (25/11/2026, con claves reales):

| IA / modelo | Entrevista | Perfil | Evaluación | Nota |
|---|---|---|---|---|
| Gemini Flash Lite | buena, 2 a 5 s | buena | buena, pero a veces **incompleta** (ver §6.4) | muy inestable ese día: 503 y timeouts |
| Gemini 3.5 Flash | buena | **la mejor** | buena | 10 a 50 s por pedido |
| Groq `gpt-oss-120b` | 1 s, buen español | no probado a fondo como primera opción | 10 de 10 en 5 s, con criterio | 8.000 tokens/min |
| Groq `gpt-oss-20b` | 1 s, a veces repite una pregunta | **mala**: requisitos vagos ("GPU", "CPU") y modelos inventados | rechaza tandas largas | por eso el perfil no va primero a Groq |
| OpenRouter Nemotron | 9 a 45 s | no probado | 180 a 250 s por 30: **inservible** | sólo último recurso |
| Groq `qwen/qwen3.8-27b` | — | — | devolvió 0 de 10 | sacado de la lista |

## 3. Reparto de tareas entre IA

Patrón: **enrutamiento por tarea con respaldo** (`ChainAI` en `core/ai.py`). Cuatro mecanismos, en este orden:

1. **Ruta por tarea.** Cada tarea tiene su orden de preferencia (`settings.DEFAULT_ROUTES`):

   | Tarea | Orden | Por qué |
   |---|---|---|
   | `interview` | Groq → Gemini → OpenRouter | pedidos chicos y frecuentes; Groq responde en ~1 s y tiene su **propia cuota**, así que no gasta la de Gemini |
   | `profile` | Gemini → Groq → OpenRouter | es el paso donde más pesa la calidad (requisitos precisos); un modelo chico lo hizo mal |
   | `evaluate` | Gemini → Groq → OpenRouter | pedidos grandes: Gemini tiene 250.000 tokens/min contra los 8.000 de Groq |

   Se cambia por entorno: `AI_ROUTE_INTERVIEW`, `AI_ROUTE_PROFILE`, `AI_ROUTE_EVALUATE` (lista separada
   por comas). `AI_CHAIN` define qué IA se consideran y el orden general para tareas sin ruta propia.
2. **Salud.** La IA con menos de la mitad de aciertos en sus últimas 8 consultas de los últimos 10
   minutos (`usage.HEALTH_MIN`) pasa al **final** del orden, sin sacarla. Se recupera sola cuando vuelve a
   acertar. Los 429 cuentan como fallo.
3. **Cuota.** Antes de consultar un modelo, se verifica que el pedido entre en lo que le queda (§4). Si no
   entra, **ni se intenta**: un 429 cuesta tiempo y a veces cuenta contra el límite.
4. **Caída.** Si una IA falla o no tiene cuota, se prueba la siguiente. Dentro de cada IA, los modelos se
   prueban en orden y el que da 429 se pausa el tiempo que indica el servicio (`retryDelay`; una hora si
   es el cupo diario; 30 s para 503).

Qué **no** hacemos, a propósito: repartir la carga entre varias claves o proyectos de una misma IA para
esquivar el límite (los límites son por proyecto y sería violar los términos del servicio).

## 4. Control de cuota y registro de uso

`core/usage.py` + `config/ai_limits.json`.

- **Registro.** Cada consulta guarda: IA, modelo, tarea, tokens de entrada, de salida y de razonamiento
  (los devuelven las dos APIs), estado HTTP y duración. Vive en memoria (últimas 2.000) y se persiste en la
  tabla `ai_usage` (Neon) para reconstruir lo gastado hoy si el servidor se reinicia.
- **Límites.** `config/ai_limits.json` tiene, por IA y modelo (patrón `fnmatch`), consultas por minuto
  (`rpm`), tokens por minuto (`tpm`) y consultas por día (`rpd`) del plan gratis, y un margen de seguridad
  (`margin`: 0,85). Si Google o Groq cambian los límites se edita ese archivo; no hace falta tocar código.
- **Admisión.** `ledger.fits(proveedor, modelo, tokens_estimados)`: un pedido entra si, sumado a lo gastado
  en el último minuto y en el día, no supera el margen de ninguno de los tres límites. La estimación
  (`estimate_call`) usa ~3,2 caracteres por token para la entrada y una salida esperada por tarea
  (`OUT_ESTIMATE`).
- **Día de Gemini.** Se reinicia a medianoche de California (hora del Pacífico, ~5 AM en Argentina).
- **Ver el consumo.** `GET /api/usage` (pide el código de acceso si está configurado) devuelve tokens
  promedio por tarea y modelo, cuota usada contra el límite y consultas fallidas.

Límites de los planes gratis (verificados el 25/11/2026 salvo lo marcado):

| Modelo | Por minuto | Por día | Fuente |
|---|---|---|---|
| Gemini 3.1 / 3.5 Flash Lite | 15 consultas, 250.000 tokens | 500 | AI Studio → Límite de frecuencia |
| Gemini 3 / 3.6 / 3.8 Flash | 5 consultas, 250.000 tokens | 20 | ídem |
| Groq (por modelo) | 8.000 tokens | 1.000 consultas | cabeceras `x-ratelimit-*` |
| OpenRouter `:free` | 20 consultas (**sin verificar**) | 50 (**sin verificar**) | de memoria |

## 5. Presupuesto de tokens (medido)

Medición real del 25/11/2026 (Gemini muy inestable ese día; los 503 no cuestan tokens pero sí tiempo).
Los tokens son los que informa la API, no estimaciones.

**Entrevista** (5 turnos, pedido detallado de notebook):

| | Antes | Ahora |
|---|---|---|
| Prompt del sistema | 2.629 tokens en **cada** turno | 1.231 en los 3 primeros turnos (sin la parte del perfil), 2.629 desde ahí |
| Total de la entrevista | ~15.000 a 17.000 tokens | ~9.500 a 10.500 tokens (**−35 %**) |

**Búsqueda completa** (150 publicaciones evaluadas, 5 tandas de 30):

| | Consultas | Tokens entrada | Tokens salida | Tiempo |
|---|---|---|---|---|
| 1ª búsqueda (caché vacía) | 8 a 17 (hasta 12 fallidas por 503/timeout) | ~19.000 | ~25.000 | 120 a 170 s |
| 2ª búsqueda, mismo perfil (caché tibia) | 2 a 3 | ~7.000 | ~10.000 | 72 a 80 s |

Lectura de los números:
- La **salida** pesa más que la entrada en la evaluación: es 1 elemento por publicación. Se la acota pidiendo
  respuesta mínima para las de puntaje bajo (§6.5).
- Antes de exigir "una respuesta por publicación" la salida era ~10.000 tokens, pero la IA **omitía
  publicaciones sin avisar** (a veces 2 de 30, o 0). Ahora se paga completo y se encuentran más modelos
  (3 que cumplen todo en una corrida, antes 0 o 1).
- Un día de uso normal: una entrevista + una búsqueda ≈ 55.000 tokens y ~12 consultas. Con 500
  consultas/día por modelo Flash Lite (3 modelos) y Groq para las preguntas, la cuota diaria alcanza para
  decenas de búsquedas; el límite real de hoy es la **estabilidad de Gemini**, no la cuota (estimación,
  no medida a escala).

## 6. Técnicas de ahorro

Cada una con su patrón conocido, dónde está y qué ahorra.

1. **Prompt en dos partes** (*progressive disclosure*). `interview.system_prompt(allow_profile)`: los
   primeros `MIN_QUESTIONS` (3) turnos no pueden cerrar con perfil, así que no se manda la parte del perfil
   (~1.400 tokens, 54 % del prompt). Ahorra ~4.200 tokens por entrevista.
2. **Caché de evaluaciones** (*memoization*, `candidates._eval_cache`). Clave = hash(perfil + título
   normalizado + ficha). TTL 6 h, hasta 5.000 entradas. La misma publicación en otra tienda, un reintento o
   un cambio de código postal no vuelve a gastar tokens. También se recuerdan las descartadas por no ser el
   producto. Ahorró ~62 % de los tokens en la 2ª búsqueda medida.
3. **Una evaluación por título.** Las tiendas de un mismo grupo (On City, Provincia, Carrefour) comparten
   catálogo: se evalúa la más barata y las demás heredan el resultado (`add_siblings`).
4. **Completitud verificada con reintento dirigido.** Cada publicación debe quedar respondida (evaluada o
   `{"id": n, "product": false}`). Lo que faltó se pide de nuevo **sólo eso**, en tandas de 10
   (`evaluate_batch`). Evita perder candidatos en silencio y no repite lo ya contestado.
5. **Salida mínima para lo que falla feo.** El prompt pide, para puntaje menor a 30, sólo `id`, `model`,
   `score` y `fails_must` (sin `meets`, `doubts` ni `reason`): la interfaz sólo muestra detalle desde 25.
6. **Tandas y división.** Se evalúa de a 30 en paralelo (5); si una IA rechaza una tanda por larga (Groq:
   HTTP 400 `json_validate_failed`), se divide en mitades hasta 8 publicaciones, y ese error no pausa al
   modelo.
7. **Razonamiento corto.** Los modelos `gemini-3*` usan `thinkingLevel: low`: ~2 s en vez de ~3,5 s por
   pregunta con la misma calidad en la entrevista. La evaluación usa temperatura 0,1 (resultados
   estables); la entrevista, 0,4.
8. **Filtros sin IA antes de la IA.** Duplicados, usados/outlet, fuera de presupuesto, disponibilidad por
   código postal y relevancia (`prefilter`) se resuelven con código. La IA ve como máximo 150
   publicaciones (`MAX_LISTINGS_TO_EVALUATE`).
9. **Seguir precio no usa IA.** El escaneo de cada 3 horas (`core/watch.py`) sólo busca y compara precios.

No implementado (a propósito o por ahora): caché semántica entre perfiles parecidos (riesgo de mezclar
personas), API de lotes de Gemini (más barata pero asíncrona: no sirve para una conversación), caché de
prompt explícita (según la documentación de Google, Gemini cachea el prefijo repetido en forma implícita;
no lo verificamos acá, y de todas formas la cuota gratis parece contar todos los tokens), *streaming*.

## 7. Catálogo de prompts

Todos están en español rioplatense, piden **sólo JSON** y se validan en código (`interview.normalize*`,
`candidates._evaluate_once`): **lo que devuelve la IA nunca llega a la interfaz sin pasar por una
validación** que recorta, descarta y limita.

### 7.1 Entrevista — preguntas (`SYSTEM_QUESTIONS` + `QUESTION_FORMAT`)
- **Cuándo:** turnos en que todavía no se puede cerrar (menos de `MIN_QUESTIONS`). Tarea `interview`.
- **Entrada:** la conversación completa (texto inicial del usuario, y turnos previos de la IA guardados
  como el JSON que devolvió).
- **Salida:** `{"type":"question","explanation","question","help","options","multi","draft"}`.
- **Qué le dice y por qué:**
  - *Pensá primero los 5 a 7 factores de decisión de la categoría, como un vendedor experto*, con
    ejemplos (monitor, mouse, secador). Antes la IA preguntaba pocas cosas básicas y faltaban factores
    (¿2K o Full HD?, ¿curvo?, ¿marca?).
  - *Una pregunta por vez, de 5 a 10, sin cerrar antes de cubrir los factores.* Una entrevista corta da un
    perfil genérico.
  - *Preguntá por el uso y las consecuencias prácticas, no por especificaciones* ("¿te molesta el ruido?",
    no "¿qué switches?"). El usuario no es experto. Las decisiones técnicas se presentan con lo que cambian
    y una recomendación.
  - *No repitas lo que ya dijo, pero hacé al menos 3 preguntas aunque el pedido sea detallado.*
  - *"No sé, explicame" → `explanation` de 2 a 4 oraciones y repetí la pregunta.* Es el corazón educativo.
  - *`draft`: lo que ya sabés, en etiquetas cortas.* Alimenta el panel "Lo que sé hasta ahora".
- **Validación:** `normalize()` recorta textos, deduplica opciones (máx. 6), exige `question`.

### 7.2 Entrevista — perfil (`SYSTEM_PROFILE`)
- **Cuándo:** desde `MIN_QUESTIONS` preguntas, al apretar "Ya está, mostrame el resultado" (`finish`) o al
  llegar a `MAX_QUESTIONS` (se fuerza con un mensaje de sistema). Tarea `profile`.
- **Salida:** `{"type":"profile","profile":{product, category, summary, must[], nice[], avoid_paying_for[],
  budget{level,max_ars,note}, search_queries[], suggested_models[], spec_queries[], exclude_words[]}}`.
- **Qué le dice y por qué:**
  - *Lo pedido con números o nombres va tal cual a `must`* ("32 GB de RAM o más"). Si no, la IA resumía y
    perdía requisitos.
  - *Cada característica con `what_is` y `why` citando lo que dijo la persona.* Es la parte explicativa.
  - *`category` una de la lista de `config/stores.json`.* Define en qué tiendas se busca.
  - *`search_queries`: AMPLIAS, tipo de producto + como mucho una palabra que aparezca en títulos reales*
    ("mouse inalambrico", no "mouse muchos botones"): el buscador de la tienda busca literalmente en los
    títulos. El filtro fino lo hace la evaluación.
  - *`suggested_models`: 4 a 8 modelos concretos en distintos rangos, sólo si está seguro.* Se buscan en las
    tiendas: si no aparecen, no se recomiendan (así la IA no puede inventar productos).
  - *`spec_queries`: una búsqueda por cada variante de una especificación dura* ("RTX 4050 para arriba" →
    `notebook rtx 4050`, `notebook rtx 5050`...): traen publicaciones que las amplias no traen.
- **Validación:** `normalize_profile()` fuerza la categoría a una válida, elimina de `search_queries` los
  modelos y las frases de más de 5 palabras, y recorta todas las listas.

### 7.3 Mensajes de sistema inyectados
`FORCE_PROFILE` (se acabaron las preguntas), `WANTS_RESULTS` (la persona quiere ver resultados) y
`TOO_SOON` (la IA cerró antes de tiempo: pedir una pregunta más). Se agregan como último turno del usuario.

### 7.4 Evaluación (`EVAL_SYSTEM`)
- **Cuándo:** por cada tanda de hasta 30 publicaciones. Tarea `evaluate`, temperatura 0,1.
- **Entrada:** el perfil como texto (`profile_text`) y una línea por publicación:
  `id | título | $precio | tienda [| ficha: especificaciones de la tienda]`.
- **Salida:** `{"items":[{id, model, score, fails_must[], fix, meets[], doubts[], reason} | {id, product:false}]}`.
- **Qué le dice y por qué:**
  - *Un elemento por cada publicación, sin saltear ninguna; si no es el producto, `{"id":n,"product":false}`.*
    Hace verificable la completitud (§6.4).
  - *`model`: marca + código, o marca + lo que distingue la configuración; nunca "MSI Gamer".* Es la clave
    para agrupar la misma cosa de distintas tiendas.
  - *Si el título contradice un obligatorio, el puntaje no pasa de 40 y va en `fails_must`.* Antes una
    MSI con Ryzen 5 salía 90 % pidiendo Ryzen 7. **El tope lo vuelve a aplicar el código**
    (`FAIL_CAP`), porque la IA a veces puntúa alto igual.
  - *Equivalencias de gama* (i7 = Core Ultra 7 = Ryzen AI 7 = "Ryzen 7 o superior"; una placa mayor cumple
    "o superior").
  - *`fix`: sólo si lo único que falta es RAM o disco y sabe que se puede ampliar.* El código lo descarta si
    falla otra cosa (`FIXABLE_RE`) y valida los valores permitidos.
  - *Nunca digas "es la única" ni "no existe":* sólo ve una parte del mercado.
  - *Para puntaje menor a 30, sólo `id`, `model`, `score`, `fails_must`.* Ahorra salida (§6.5).
- **Post-proceso en código:** tope `FAIL_CAP`, validación de `fix`, `score` entre 0 y 100, descarte de ids
  inexistentes, lista de publicaciones sin respuesta para el reintento.

### 7.5 Ficha del fabricante (`core/specs.py`, `PROMPT`)
- **Cuándo:** para los "casi cumplen" que se podrían completar. Gemini + búsqueda de Google. **No pasa
  por `get_ai()` ni por el registro de uso**: `specs.lookup()` llama directo a la API de Gemini (pendiente:
  registrarlo en `usage.ledger` cuando se habilite).
- **Salida:** `{hz, ram_slots, ram_max_gb, ram_soldered, m2_slots}` con `null` en lo no confirmado.
- **Regla central:** *si no podés confirmarlo con una fuente, `null`.* Sin fuente (`groundingChunks`) todo
  queda "sin confirmar" y nunca se inventa. Se guarda en `model_specs` con sus fuentes.

## 8. Reglas para tocar un prompt

1. **Medí antes y después** (`GET /api/usage` o `usage.ledger.summary()`): tokens por tarea y calidad de
   salida. Un prompt más largo se paga en **cada** turno de **cada** persona.
2. **Probá con los casos que ya fallaron:** pedido muy detallado de notebook (no debe saltear preguntas ni
   perder requisitos), monitor para oficina, mouse ergonómico, secador de pelo.
3. **La validación en código es la última defensa**, no la IA: si agregás un campo, agregá su
   validación en `normalize*` o `_evaluate_once` y un test.
4. **No metas datos del usuario en el prompt del sistema:** va en los mensajes (así el prompt es estable y
   se puede cachear).
5. **Documentá acá el cambio**: qué problema resuelve, qué mediste.

## 9. Límites conocidos

- **Gemini gratis es inestable:** el 25/11/2026, 12 de 17 consultas devolvieron 503 o timeout. La cadena
  lo cubre con Groq, pero Groq (8.000 tokens/min) sólo carga con ~10 a 20 publicaciones por minuto.
  Habilitar la facturación en Google AI Studio sacaría los límites gratis y mejoraría la estabilidad.
- **La evaluación varía entre modelos:** la misma búsqueda dio 0 y 3 modelos que cumplen en dos corridas
  según qué modelo respondió. Temperatura baja ayuda, no elimina.
- **La búsqueda de Google dentro de Gemini no está en el plan gratis** (429): las fichas del fabricante
  quedan "sin confirmar". Se activa sola al habilitar facturación.
- **Los planes gratis pueden usar lo que se les manda para entrenar sus modelos.** No se mandan datos
  personales: sólo texto de compras.
- **OpenRouter gratis es lento** (9 a 250 s): último recurso.
- **Los límites de OpenRouter en `ai_limits.json` están sin verificar.**
- **El conteo de cuota es por proceso** (memoria + base): con más de una instancia del servidor se
  subestimaría lo gastado (hoy Render corre una).
