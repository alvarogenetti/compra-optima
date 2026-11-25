# Tiendas: de dónde salen los precios

Registro en `config/stores.json` (21 tiendas, 11 categorías). Lectores en `core/search.py`. Mercado Libre,
en `core/comparaya.py`.

- [1. Plataformas y cómo se lee cada una](#1-plataformas-y-cómo-se-lee-cada-una)
- [2. Registro de tiendas](#2-registro-de-tiendas)
- [3. Particularidades de Frávega](#3-particularidades-de-frávega)
- [4. Mercado Libre vía ComparaYa](#4-mercado-libre-vía-comparaya)
- [5. Reglas que no se rompen](#5-reglas-que-no-se-rompen)
- [6. Cómo sumar una tienda](#6-cómo-sumar-una-tienda)

## 1. Plataformas y cómo se lee cada una

Cada plataforma publica el buscador que usa su propia web. No hace falta navegador ni se evade ningún
control.

| Plataforma | Endpoint | Tiendas |
|---|---|---|
| VTEX | `/api/catalog_system/pub/products/search?ft=<texto>&_from&_to[&fq=P:[a TO b]]` (JSON, de a 50) | Frávega, On City, Cetrogar, Carrefour, Pardo, Coppel, Naldo, Jumbo, Provincia Compras, Samsung Shop, Diamond |
| WooCommerce | `/wp-json/wc/store/v1/products?search=<texto>&per_page=50` (JSON, Store API) | Comeros, ISInet, Aura Gamer, Espaciotek, Zonetech, Hardcore, DeCaba |
| Tiendanube | `/search/?q=<texto>` (HTML con `data-variants`, precio por transferencia) | Nickhard, Shopgamer, Dualkey |

Normalización a un único formato de publicación (`listing`):
`{store, store_name, title, brand, price, list, cuotas, promo, url, query}` (y en VTEX además `vtex` y `specs`
para disponibilidad y ficha). Precios: `core/price_parser.py` entiende el formato argentino ("$ 1.234,56").

**VTEX, detalles:**
- Por publicación se toma el vendedor más barato **con stock** (`AvailableQuantity > 0`).
- `Price + Tax`: el precio real (ver §3). En las tiendas donde `Tax` viene en 0 no cambia nada.
- Se informan cuotas sin interés (`Installments` sin `InterestRate`) y la promoción (`Teasers`).
- Las especificaciones de la ficha (`allSpecifications`) se filtran por palabras útiles (`SPEC_KEYS`) y se
  pasan a la evaluación.

## 2. Registro de tiendas

`config/stores.json`:

```json
{"id": "fravega", "name": "Frávega", "platform": "vtex", "host": "www.fravega.com",
 "categories": ["informatica", "gaming", ...], "notes": "...", "cloud_blocked": false}
```

- `categories`: qué vende. La IA elige **una** categoría del perfil y sólo se busca en esas tiendas ("no
  vas a buscar un mouse en una tienda de cosméticos"). Las 11 categorías tienen ejemplos en el mismo archivo
  (`informatica`, `gaming`, `celulares`, `audio-tv`, `electrodomesticos`, `pequenos-electro`,
  `cuidado-personal`, `hogar`, `herramientas`, `deportes`, `juguetes-bebes`).
- `cloud_blocked: true`: la tienda responde 403 a los servidores en la nube. **No se consulta desde Render
  ni GitHub Actions** (`stores.running_in_cloud()`: variables `RENDER` o `GITHUB_ACTIONS`), sí desde una PC.
  Hoy: Comeros, DeCaba, Hardcore Computación (verificado en noviembre de 2026).
- Cobertura real: hay muchas tiendas de informática y gaming, y **pocas generalistas** para el resto de las
  categorías (belleza, deportes, hogar): ahí la calidad de los resultados depende de las 11 tiendas VTEX.

## 3. Particularidades de Frávega

Verificadas en su web el 25/11/2026 (documentadas porque cuesta mucho descubrirlas):

1. **El `Price` de su API viene SIN IVA** y el impuesto aparte en `Tax`: el precio real es la suma
   (ej. $1.979.418 + $207.839 = $2.187.257, lo que muestra la web).
2. **Dejó VTEX para su web: el `link` de la API lleva a una página vacía.** Las fichas reales son
   `https://www.fravega.com/p/<nombre del producto>-<itemId>/` (slug del `productName` + `itemId`, no
   `productId`). `search.product_url()` lo arma.
3. **La API muestra con stock cosas que no se pueden comprar:** publicaciones "no disponibles para tu
   ubicación" y "Compra Internacional" (importadas por vendedores del marketplace). Lo primero se resuelve
   con la simulación de compra ([SEARCH.md §5](SEARCH.md#5-disponibilidad-en-tu-zona)); lo segundo no se
   puede distinguir en los datos.
4. Responde desde Render y, a veces, desde GitHub Actions (en el tracker la bloqueaba).

## 4. Mercado Libre vía ComparaYa

**Mercado Libre no se lee directo.** Sus términos prohíben robots y scraping, y su API cierra el acceso a
las apps que sólo leen precios (el tracker lo comprobó el 23/11/2026). Tampoco se usan intermediarios que lo
scrapean (Jina Reader). Ver [DECISIONS.md](DECISIONS.md) ADR-005.

**Lo que sí se hace:** [ComparaYa](https://comparaya.net) es un comparador afiliado de Mercado Libre que
publica el precio de ML junto al de otras tiendas en cada **ficha de producto** (`/p/<slug>`). Su
`robots.txt` permite las fichas y prohíbe `/api/` (su buscador): se leen **sólo fichas**, nunca el buscador.

- **Cómo se encuentra la ficha:** se arma la dirección con tipo, marca y código, y se prueban hasta 8
  variantes (4 en la búsqueda en vivo) con 1,2 s de pausa. Ejemplos: `monitor-msi-mp241-24`,
  `mouse-logitech-g502-lightspeed`.
- **Notebooks y PCs** las nombra por **línea + procesador**, una ficha por procesador:
  `notebook-asus-tuf-gaming-a15-ryzen-7-7435hs`. La línea sale del nombre del modelo o del título; los
  procesadores, del título y de los vistos en publicaciones de esa línea en nuestras tiendas.
- **Qué se toma:** sólo ofertas nuevas cuyo título tiene **todas las palabras del modelo salvo la marca**
  ("G502 Hero" no pasa por "G502 Lightspeed"; la ficha junta versiones). De Mercado Libre y de tiendas que
  no leemos directo (para no duplicar las nuestras, que son más frescas). Las "Internacional" se marcan.
- **Caché:** la dirección encontrada se recuerda 30 días y el "no existe", 7, en `comparaya_slugs`. La
  clave lleva `SLUG_VERSION` (v3): al mejorar cómo se arman las direcciones se sube y los "no existe"
  viejos dejan de contar.
- **Dónde se usa:** en cada búsqueda (5 primeros modelos sugeridos), en el botón "¿Y en Mercado Libre?" y
  en el escaneo de Seguir precio (ML sin importados entra como una tienda más).
- **Límite:** sirve sólo para modelos que ComparaYa tiene; los precios pueden tener unas horas de atraso.
  Para el resto queda el link de búsqueda en ML (marca + modelo: las búsquedas amplias no devuelven nada).

Descartados (revisión hecha desde el proyecto del tracker, no verificada de nuevo acá): **Precialo** (prohíbe
leer su buscador) y **garpa.ar** (compara por EAN, no muestra Mercado Libre, y su `robots.txt` prohíbe `/buscar` y
`/api`).

## 5. Reglas que no se rompen

1. **No se evade ningún control anti-bot** (captchas, bloqueos por IP, verificaciones). Si una tienda bloquea,
   se informa como error y se sigue.
2. **No se leen sitios que lo prohíben** en su `robots.txt` o términos.
3. **Pausas entre consultas** al mismo sitio (ComparaYa: 1,2 s) y `User-Agent` de navegador normal.
4. **Una falla de tienda no corta la búsqueda:** `search_all` captura el error de cada (tienda, búsqueda) y
   lo informa en "Dónde buscamos".

## 6. Cómo sumar una tienda

1. Si es VTEX, WooCommerce o Tiendanube: agregar una entrada en `config/stores.json` con `id`, `name`,
   `platform`, `host` y `categories`. Nada más.
2. Probar a mano: `search.search_all([store], ["<producto>"])`.
3. Si bloquea a la nube, `"cloud_blocked": true` y una nota con la fecha.
4. Otra plataforma: una función `search_<plataforma>(client, store, query)` que devuelva `listing`s, y
   registrarla en `search.PLATFORMS`. Con un test que use una respuesta real guardada, sin red.
5. Actualizar este documento (tabla de §1) en el mismo commit.
