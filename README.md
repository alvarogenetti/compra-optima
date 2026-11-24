# Compra Óptima

Contás qué querés comprar ("un mouse", "una secadora de pelo para pelo largo"). La app te hace unas
preguntas simples sobre **cómo lo vas a usar**, arma tu **perfil de compra explicado** (qué tiene que
tener, qué es deseable y por qué no pagar de más) y busca **precios reales** en tiendas argentinas,
en tres rangos: económica, equilibrada y premium.

Nació del [tracker de monitores](https://github.com/alvarogenetti/monitor-precios-tracker): lo que allá
se hizo a mano (PERFIL.md → requisitos → buscar en tiendas), acá lo hace una IA para cualquier producto.

> **Estado:** prueba con amigos y familia. Además de recomendar, **sigue precios**: con "Seguir precio"
> te avisa por Telegram cuando un modelo baja de verdad (ver [Seguir precio](#seguir-precio-telegram--neon)).

## Uso

```powershell
.\iniciar.ps1
```

Crea el entorno la primera vez, copia `.env.example` a `.env` y abre http://127.0.0.1:8010.

- **Con IA (recomendado):** sacá una clave gratis en https://aistudio.google.com/apikey y ponela en
  `GEMINI_API_KEY` dentro de `.env`.
- **Sin clave:** arranca en **modo demo**: preguntas fijas y evaluación por palabras. Sirve para probar
  la interfaz y la búsqueda en tiendas, pero los resultados traen cosas que no son el producto
  (sin IA no hay quien las filtre).

`GEMINI_MODEL` es una lista en orden de preferencia: primero `gemini-3-flash-preview` (unos 2 s por
pregunta), después los "lite" (3-4 s) y al final `gemini-3.5-flash` (anda, pero tarda 10-18 s) y
`gemini-flash-latest`. **Cada modelo tiene su propio cupo gratis**, así que con varios en la lista se
aguanta mucho más. Si uno responde 429 (límite por minuto o por día), la app lo saltea el tiempo que
indica Google (retryDelay; una hora si es el cupo diario) y usa el siguiente sin perder tiempo; si
responde 503 (saturado), lo saltea 30 s. Si están todos en pausa, el mensaje dice en cuánto probar.
La búsqueda con evaluación tarda unos 25 s.

El plan gratis de Gemini tiene límites por minuto y por día, y Google puede usar lo que se le manda
para mejorar sus modelos. Para una prueba chica alcanza; antes de abrirlo a más gente, conviene
pasar a un plan pago.

## Publicar la página (Render, gratis)

GitHub Pages no sirve: sólo publica archivos estáticos, y esta app necesita un servidor (Python, la
clave de Gemini y las búsquedas en tiendas). Render tiene un plan gratis que alcanza para la prueba.

1. Creá una cuenta en https://render.com (con tu GitHub es lo más fácil).
2. **New → Blueprint** y elegí el repo `compra-optima`. Render lee `render.yaml` y arma el servicio.
3. Te pide dos valores que no van al repo:
   - `GEMINI_API_KEY`: tu clave de AI Studio.
   - `ACCESS_CODE`: un código cualquiera (ej. `milanesa2026`) que les pasás a amigos y familia.
   - Opcional, `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`: los comentarios de **"¿Te sirvió?"** te llegan
     por Telegram (sirve el mismo bot del tracker). Sin esto, quedan en los Logs de Render.
4. Listo: queda en `https://compra-optima.onrender.com` (o parecido). Cada push a `main` se publica solo.

**Protección de la versión publicada** (`core/guard.py`): con `ACCESS_CODE` definido, la web pide el
código antes de usar la IA, y cada persona (IP) tiene un límite por hora (80 turnos de entrevista y 12
búsquedas; se cambian con `LIMIT_INTERVIEW_PER_HOUR` / `LIMIT_SEARCH_PER_HOUR`). Así nadie de afuera
te gasta el cupo gratis de Gemini. En tu PC, sin `ACCESS_CODE`, no pide nada.

**Limitaciones del plan gratis:** el servidor se duerme después de 15 minutos sin uso y la primera
visita tarda alrededor de un minuto en despertarlo. Comeros, DeCaba y Hardcore responden 403 a los
servidores en la nube (desde tu PC andan): están marcadas `"cloud_blocked": true` en `config/stores.json`
y no se consultan desde Render ni GitHub Actions. Frávega sí responde desde Render. Desde GitHub Actions
(escaneo de "Seguir precio") Frávega suele bloquear, como en el tracker. En "Dónde buscamos" se ve cuáles
respondieron.

## Seguir precio (Telegram + Neon)

**Cómo se usa.** En cada resultado, **🔔 Seguir precio** → **Abrir Telegram** → **Iniciar**. Listo: sin
cuentas ni contraseñas. El bot confirma el precio de hoy y avisa cuando ese modelo esté **al menos 15 %
más barato que su precio habitual** (🔥 excepcional desde 30 %). En el bot: `/lista` muestra lo que
seguís y `/dejar_<n>` deja de seguir. Máximo 20 productos por persona.

**Cómo funciona.**

- `core/follow.py`: la web crea el seguimiento con un token y devuelve `t.me/<bot>?start=<token>`; el
  `/start <token>` lo vincula al chat. Si alguien comparte el link, quien lo abre tiene su propio
  seguimiento. Los links que nadie abrió en una semana se borran.
- `core/watch.py` (GitHub Actions cada 3 h, `.github/workflows/watch.yml`; también a mano con "Run
  workflow"): busca cada modelo seguido **una sola vez** aunque lo sigan muchos, en las tiendas de su
  categoría. Sólo cuenta una publicación si el título tiene todas las palabras del modelo salvo la marca
  ("G502 Hero" no pasa por "G502 Lightspeed") y no es un accesorio ("Cable USB para G502", "Funda...").
  Guarda el más barato de cada tienda en el historial. **Sin IA**: no gasta cupo de Gemini.
- **Primer escaneo de cada seguimiento**: no avisa ofertas, fija el punto de partida real. La web pudo
  haber mostrado otra publicación del mismo modelo (ej. Frávega tenía el MSI PRO MP241 a $474.962 y
  a $293.716): si el escaneo lo encuentra 15 % o más barato que lo que se vio, lo cuenta como **dato**
  ("es otra publicación, no una baja") y toma ese precio como partida.
- `core/market.py` (traído del tracker): **precio habitual** = mediana del mejor precio de cada escaneo
  en 30 días, sin las últimas 24 h; hasta tener 6 escaneos en 2 días, el precio de cuando empezaste a
  seguirlo. Si la tienda anuncia un descuento grande que contra el mercado es menos de la mitad, el
  aviso dice que está inflado. La misma oferta no se repite: sólo si mejora 3 % o pasó una semana.
- `core/db.py`: Postgres de **Neon** (`DATABASE_URL`, la misma en Render y en GitHub Actions); en tu PC
  sin `DATABASE_URL`, SQLite en `data/compra.db`. Tablas `watchers`, `follows`, `prices`.
- El webhook del bot se registra solo cuando arranca en Render (`RENDER_EXTERNAL_URL`).

**Activarlo (una vez).**

1. **Bot nuevo**: en Telegram, @BotFather → `/newbot` (ej. "Compra Óptima", usuario `CompraOptimaBot`
   o el que esté libre). El token va en `COMPRA_BOT_TOKEN`. **No uses el bot del tracker**: se le
   registraría el webhook.
2. **Neon**: https://neon.tech con tu GitHub → crear proyecto (región São Paulo o US East) → copiar la
   *connection string* (`postgresql://...sslmode=require`). Va en `DATABASE_URL`.
3. **Render** (servicio → Environment): `COMPRA_BOT_TOKEN` y `DATABASE_URL`. Se redeploya solo.
4. **GitHub** (repo → Settings → Secrets and variables → Actions): los mismos dos como secrets.

GitHub desactiva los workflows programados de un repo público sin actividad en 60 días: si pasa,
se reactivan desde la pestaña Actions.

## Cómo funciona

```
Texto libre ──► Entrevista (IA) ──► Perfil de compra ──► Tiendas de esa categoría
                 una pregunta por vez   must / nice /        (config/stores.json)
                 sobre el uso,          no pagar extra,             │
                 "no sé" → explica      cada uno con su        Búsqueda real
                                        "qué es" y "por qué"   (VTEX, WooCommerce, Tiendanube)
                                                                    │
                     Resultados ◄── Rangos de precio ◄── La IA evalúa cada publicación
                     + links a ML    (agrupado por modelo)   contra el perfil
```

1. **Entrevista** (`core/interview.py`): la IA primero piensa los 5 a 7 **factores de decisión** de esa
   categoría, como un vendedor experto (monitor: uso, tamaño y resolución, Hz, plano o curvo, con qué
   lo conecta, brazo o ajuste de altura, marca), y los recorre de a una pregunta (de 5 a 10). Las
   decisiones técnicas se presentan con lo que cambian en la práctica y una recomendación ("Full HD,
   más barato · 2K, texto más nítido, ideal en 27""); lo demás, por el uso ("¿te molesta que haga
   ruido?", no "¿qué switches?"). Pregunta marca preferida y "¿algo que sí o sí quieras o no?". No
   repite lo que ya dijiste, pero aunque el primer mensaje sea muy detallado hace al menos 3 preguntas
   sobre lo que falte (si la IA cierra antes, se le pide una más). Todo lo que se pidió con números
   (32 GB, 1 TB, Ryzen 7) va tal cual a "Tiene que tener". "No sé, explicame" explica y repite la
   pregunta. Al llegar al máximo, se fuerza el perfil.
2. **Perfil**: cada característica trae *qué es* (en una oración que entienda cualquiera) y *por qué
   para vos* (citando lo que respondiste). Incluye la categoría, búsquedas para las tiendas, modelos
   sugeridos y palabras a excluir (accesorios, repuestos).
3. **Tiendas** (`config/stores.json`): cada una con las categorías que vende y su plataforma. Se busca
   sólo en las de la categoría del perfil.
4. **Búsqueda real** (`core/search.py`): el buscador público de cada plataforma, en paralelo. Los
   modelos sugeridos por la IA también se buscan: **si no aparecen en ninguna tienda, no se
   recomiendan** (así no hay productos inventados o que no se venden acá).
5. **Candidatos** (`core/candidates.py`):
   - **Búsquedas amplias** ("mouse inalambrico gamer"), nunca necesidades ("mouse muchos botones"): el
     buscador de la tienda busca las palabras literalmente en los títulos. El filtro fino lo hace la IA.
   - **Qué se evalúa** (hasta 120 publicaciones): primero hasta 6 de cada modelo sugerido (el título
     tiene que tener su código, ej. "G502"), después el resto repartido entre las búsquedas amplias y
     por precio según el presupuesto (económico mira el 60 % más barato, alto el 60 % más caro).
     Los usados, outlet, exhibición y caja abierta se descartan (mismo criterio que el tracker).
   - La IA evalúa cada publicación (¿es el producto?, puntaje, modelo normalizado con su código o con
     la configuración que lo distingue, nunca "MSI Gamer") y lista qué obligatorios **contradice** el
     título (pide Ryzen 7 y dice Ryzen 5): si contradice alguno, el código limita el puntaje a 40 (pasa
     a "Casi cumplen", con "No cumple: ..."). Se agrupa por modelo y se corta en **tres rangos por precio** (en proporción: dos modelos de precio parecido
     caen juntos). "Mi recomendación" sale del rango que va con el presupuesto.
   - **Casi cumplen**: los que tienen puntaje 35-54, con lo que les falta.

### Capa de IA

Todo pasa por `core/ai.py` (`get_ai().json(system, messages, task)`). Hoy: Gemini (gratis) y demo.
Para sumar otro proveedor alcanza con una clase con el método `json`. La conversación la guarda el
navegador y viaja completa en cada pedido: el servidor no guarda estado.

### Tiendas

| Plataforma | Cómo se lee | Tiendas |
|---|---|---|
| VTEX | `/api/catalog_system/pub/products/search?ft=` (JSON) | Frávega, On City, Cetrogar, Carrefour, Pardo, Coppel, Naldo, Jumbo, Provincia Compras, Samsung Shop, Diamond |
| WooCommerce | `/wp-json/wc/store/v1/products?search=` (JSON) | Comeros, ISInet, Aura Gamer, Espaciotek, Zonetech, Hardcore, DeCaba |
| Tiendanube | `/search/?q=` (HTML con `data-variants`) | Nickhard, Shopgamer, Dualkey |

Sumar una tienda de esas plataformas es agregar una entrada en `config/stores.json`.

**Frávega (VTEX) tiene tres particularidades** (verificadas en su web el 25/11/2026):
- El `Price` de su API viene **sin IVA** y el impuesto aparte en `Tax`: el precio real es la suma
  (ej. $1.979.418 + $207.839 = $2.187.257, lo que muestra la web). Se suma `Tax` en todas las tiendas
  VTEX (en las demás viene en 0).
- Dejó VTEX para su web: el link de la API lleva a una página vacía. Las fichas son
  `fravega.com/p/<nombre>-<número de artículo>/` (el `itemId`), y así se arma el link.
- La API sigue mostrando con stock publicaciones que en la web dicen "no disponible para tu ubicación"
  o que son "Compra Internacional" (importadas por vendedores del marketplace), y no hay forma de
  distinguirlas en los datos.

## Lo que se trajo del tracker de monitores

- Los lectores de VTEX, WooCommerce y Tiendanube, y el parser de precios en pesos (`core/price_parser.py`).
- **No se evade ningún control anti-bot.** Si una tienda bloquea, queda informada como error.
  Frávega bloquea las IPs de servidores en la nube (GitHub Actions): desde una PC anda.
- **Mercado Libre no se lee directo.** Sus términos prohíben robots y scraping, y la API cierra el
  acceso a las apps que sólo leen precios (el tracker lo comprobó en noviembre de 2026). Tampoco se usan
  intermediarios como Jina Reader. En cambio, como el tracker, se usa **ComparaYa** (`core/comparaya.py`):
  comparador afiliado de ML que publica sus precios en fichas que su robots.txt permite leer (prohíbe
  `/api/`, su buscador, así que no se usa). La ficha se encuentra armando la dirección con tipo, marca y
  código (`monitor-msi-mp241-24`, `mouse-logitech-g502-lightspeed`): se prueban hasta 6 variantes con
  1,2 s de pausa y la encontrada (o "no existe") se recuerda 30/7 días en la tabla `comparaya_slugs`.
  Sólo cuentan ofertas nuevas con todas las palabras del modelo salvo la marca. Las **notebooks y PCs**
  las nombra por línea + procesador (`notebook-asus-tuf-gaming-a15-ryzen-7-7435hs`, una ficha por
  procesador): la línea sale del nombre del modelo o del título, y se prueban los procesadores que
  aparecen en publicaciones de esa línea en nuestras tiendas. La caché tiene versión (`SLUG_VERSION`):
  al mejorar cómo se arman las direcciones, los "no existe" viejos dejan de contar. Se usa:
  - en **cada búsqueda**: los primeros 5 modelos sugeridos se buscan en ComparaYa (de a 3 en paralelo)
    y sus ofertas (ML y tiendas que no leemos) entran a la evaluación como cualquier publicación;
  - en el botón **"¿Y en Mercado Libre?"** de cada resultado;
  - en el escaneo de Seguir precio (ML sin importados entra como una tienda más).
  Los links "Mirá también en Mercado Libre" son de marca + modelo (las búsquedas amplias en ML no
  devuelven nada). Precialo no se usa: prohíbe leer su
  buscador.
- Para la etapa de seguimiento se pueden traer la oferta real contra la mediana de 30 días
  (`services/market.py`), los eventos con referencias congeladas y las cuotas en pesos de hoy.

## Próximas etapas

1. Probar con amigos y familia y ajustar los prompts con lo que llegue por "¿Te sirvió?".
2. Seguir precio: historial en la web (gráfico como el del tracker), "avisame si baja de $X",
   eventos (Black Friday, Cyber Monday) con referencias congeladas, cuotas en pesos de hoy.
3. Más tiendas y categorías (belleza, deportes, hogar), oferta real, eventos, cuotas.
4. Mercado Libre vía **ComparaYa** (como en el tracker desde el 24/11/2026: comparador afiliado de ML
   cuyas fichas públicas permite leer su robots.txt). Acá es más difícil porque la dirección de la ficha
   se arma con marca y código, y sirve sólo para los modelos que ya encontramos.
5. Monetización: links de afiliado (sin cobrar por destacar productos: la recomendación tiene que
   ser neutral).

## Estructura

```
app/        FastAPI (main.py) y la página (static/: index.html, app.js, styles.css)
core/       ai (proveedores), demo, interview, stores, search, candidates, guard, feedback,
            db, follow, telegram, market, watch, comparaya, price_parser, settings
.github/    workflows: watch (escaneo cada 3 h) y tests
config/     stores.json: tiendas y categorías
tests/      pytest sin red (IA y tiendas con dobles)
```

```powershell
.venv\Scripts\python.exe -m pytest -q
```
