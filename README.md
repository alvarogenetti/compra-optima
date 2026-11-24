# Compra Óptima

Contás qué querés comprar ("un mouse", "una secadora de pelo para pelo largo"). La app te hace unas
preguntas simples sobre **cómo lo vas a usar**, arma tu **perfil de compra explicado** (qué tiene que
tener, qué es deseable y por qué no pagar de más) y busca **precios reales** en tiendas argentinas,
en tres rangos: económica, equilibrada y premium.

Nació del [tracker de monitores](https://github.com/alvarogenetti/monitor-precios-tracker): lo que allá
se hizo a mano (PERFIL.md → requisitos → buscar en tiendas), acá lo hace una IA para cualquier producto.

> **Estado:** prototipo para probar con amigos y familia. Todavía no sigue precios en el tiempo
> (el botón "Seguir precio" está deshabilitado): eso es la siguiente etapa.

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

`GEMINI_MODEL` es una lista en orden de preferencia (por defecto `gemini-3-flash-preview`, después los
"lite" y `gemini-flash-latest`). Si uno está saturado (pasa seguido en el plan gratis), sin cupo o
retirado, la app usa el siguiente. Con `gemini-3-flash-preview` cada pregunta tarda unos 2 s, el perfil
unos 7 s y la búsqueda con evaluación unos 25 s.

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
4. Listo: queda en `https://compra-optima.onrender.com` (o parecido). Cada push a `main` se publica solo.

**Protección de la versión publicada** (`core/guard.py`): con `ACCESS_CODE` definido, la web pide el
código antes de usar la IA, y cada persona (IP) tiene un límite por hora (80 turnos de entrevista y 12
búsquedas; se cambian con `LIMIT_INTERVIEW_PER_HOUR` / `LIMIT_SEARCH_PER_HOUR`). Así nadie de afuera
te gasta el cupo gratis de Gemini. En tu PC, sin `ACCESS_CODE`, no pide nada.

**Limitaciones del plan gratis:** el servidor se duerme después de 15 minutos sin uso y la primera
visita tarda alrededor de un minuto en despertarlo. Frávega bloquea a los servidores en la nube, así
que es probable que ahí no responda (en "Dónde buscamos" aparece como error; el resto sigue).

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

1. **Entrevista** (`core/interview.py`): la IA hace de 3 a 8 preguntas sobre el uso, nunca sobre
   especificaciones ("¿te molesta que haga ruido?", no "¿qué switches?"). No repite lo que ya dijiste.
   "No sé, explicame" devuelve una explicación simple y repite la pregunta. Al llegar al máximo, se
   fuerza el perfil.
2. **Perfil**: cada característica trae *qué es* (en una oración que entienda cualquiera) y *por qué
   para vos* (citando lo que respondiste). Incluye la categoría, búsquedas para las tiendas, modelos
   sugeridos y palabras a excluir (accesorios, repuestos).
3. **Tiendas** (`config/stores.json`): cada una con las categorías que vende y su plataforma. Se busca
   sólo en las de la categoría del perfil.
4. **Búsqueda real** (`core/search.py`): el buscador público de cada plataforma, en paralelo. Los
   modelos sugeridos por la IA también se buscan: **si no aparecen en ninguna tienda, no se
   recomiendan** (así no hay productos inventados o que no se venden acá).
5. **Candidatos** (`core/candidates.py`): prefiltro sin IA (relevancia, tope de presupuesto), la IA
   evalúa cada publicación (¿es el producto?, puntaje, modelo normalizado), se agrupa por modelo (la
   misma cosa en varias tiendas) y se corta en tercios de precio. "Mi recomendación" es el mejor
   puntaje del rango equilibrado.

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

## Lo que se trajo del tracker de monitores

- Los lectores de VTEX, WooCommerce y Tiendanube, y el parser de precios en pesos (`core/price_parser.py`).
- **No se evade ningún control anti-bot.** Si una tienda bloquea, queda informada como error.
  Frávega bloquea las IPs de servidores en la nube (GitHub Actions): desde una PC anda.
- **Mercado Libre no se lee.** Sus términos prohíben robots y scraping, y la API cierra el acceso
  a las apps que sólo leen precios (el tracker lo comprobó en noviembre de 2026). Tampoco se usan
  intermediarios como Jina Reader, porque sería lo mismo. Se ofrece la búsqueda armada como link.
  A futuro: averiguar si el programa de afiliados de ML permite mostrar precios.
- Para la etapa de seguimiento se pueden traer la oferta real contra la mediana de 30 días
  (`services/market.py`), los eventos con referencias congeladas y las cuotas en pesos de hoy.

## Próximas etapas

1. Probar la entrevista con productos reales y ajustar los prompts.
2. Seguimiento de precios para varios usuarios (base de datos, escaneos programados, avisos).
3. Más tiendas y categorías (belleza, deportes, hogar), oferta real, eventos, cuotas.
4. Monetización: links de afiliado (sin cobrar por destacar productos: la recomendación tiene que
   ser neutral).

## Estructura

```
app/        FastAPI (main.py) y la página (static/: index.html, app.js, styles.css)
core/       ai (proveedores), demo, interview, stores, search, candidates, price_parser, settings
config/     stores.json: tiendas y categorías
tests/      pytest sin red (IA y tiendas con dobles)
```

```powershell
.venv\Scripts\python.exe -m pytest -q
```
