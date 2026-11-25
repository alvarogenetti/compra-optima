# Del pedido a la recomendación: entrevista, búsqueda, evaluación y rangos

Cómo se llega de "quiero un mouse" a "estas son tus opciones". Los números entre paréntesis son
constantes del código (se citan para que el documento sea verificable; si cambian, se actualizan acá).
La capa de IA está en [AI.md](AI.md); las tiendas, en [STORES.md](STORES.md).

- [1. Entrevista y perfil](#1-entrevista-y-perfil)
- [2. Búsqueda en las tiendas](#2-búsqueda-en-las-tiendas)
- [3. Prefiltro (sin IA)](#3-prefiltro-sin-ia)
- [4. Evaluación](#4-evaluación)
- [5. Disponibilidad en tu zona](#5-disponibilidad-en-tu-zona)
- [6. Rangos y recomendación](#6-rangos-y-recomendación)
- [7. Casi cumplen y cuánto cuesta completar](#7-casi-cumplen-y-cuánto-cuesta-completar)
- [8. Mercado Libre](#8-mercado-libre)

## 1. Entrevista y perfil

`core/interview.py`. La IA hace entre `MIN_QUESTIONS` (3) y `MAX_QUESTIONS` (10) preguntas, una por vez,
sobre el uso. Después devuelve el **perfil**:

| Campo | Para qué se usa |
|---|---|
| `must` / `nice` / `avoid_paying_for` | se muestran con su "qué es" y "por qué para vos"; `must` y `nice` también alimentan la evaluación |
| `category` | elige las tiendas (`config/stores.json`) |
| `search_queries` | búsquedas **amplias** en las tiendas |
| `suggested_models` | se buscan por nombre; si no aparecen en ninguna tienda no se recomiendan |
| `spec_queries` | una búsqueda por cada variante de una especificación dura ("notebook rtx 4050", "notebook rtx 5050"...) |
| `exclude_words` | palabras de lo que NO es el producto (accesorios) |
| `budget` | `level` (económico / medio / alto) y `max_ars` si dio un tope |

`normalize_profile()` valida todo: categoría válida, sin modelos ni frases de más de 5 palabras en
`search_queries`, listas recortadas. El texto exacto de los prompts y el porqué de cada regla están en
[AI.md §7](AI.md#7-catálogo-de-prompts).

## 2. Búsqueda en las tiendas

`candidates.find()` → `search.search_all()`.

- **Qué se busca:** `search_queries` + `spec_queries` + `suggested_models`, en cada tienda de la categoría,
  en paralelo (16 hilos). Cada tienda se lee con el buscador público de su plataforma (VTEX, WooCommerce,
  Tiendanube); ver [STORES.md](STORES.md).
- **Filtro de precio de la tienda (VTEX):** `fq=P:[1000 TO tope×1,3]`. "notebook rtx 4050" en Frávega pasa
  de 1.057 resultados a 277, y la API devuelve de a 50, así que sin filtro las publicaciones del rango
  buscado quedaban fuera de la primera página.
- **Dos páginas (100 resultados)** para las `spec_queries`; una para el resto.
- **Excluidos:** usados, outlet, exhibición, caja abierta (`search.USED_RE`, mismo criterio que el
  tracker) y las `exclude_words` del perfil.

**Por qué búsquedas amplias:** el buscador de una tienda busca las palabras *literalmente* en los títulos, y
los títulos dicen tipo de producto y datos ("Mouse Gamer Inalámbrico Logitech G502 Lightspeed"), nunca
necesidades. Una búsqueda como "mouse muchos botones" no trae nada. El filtro fino lo hace la
evaluación.

**Por qué `spec_queries`:** una notebook (ASUS TUF FA607NUQ, RTX 4050, $2.711.999) no aparecía porque las
búsquedas amplias no la traían. Una búsqueda por la especificación dura sí.

## 3. Prefiltro (sin IA)

`candidates.prefilter()`. La IA no puede ver todo (cuesta tokens): se eligen hasta `MAX_LISTINGS_TO_EVALUATE`
(150) con reglas deterministas, en este orden:

1. **Presupuesto:** fuera lo que pasa `tope × 1,3`.
2. **Duplicados:** una publicación por título normalizado (`title_key`); la más barata. Las tiendas de un
   mismo grupo comparten catálogo. Las demás se reincorporan después (`add_siblings`).
3. **Modelos sugeridos primero:** hasta `MODEL_QUOTA` (6) publicaciones por modelo, **repartidas por
   precio** (`spread`). El título debe tener el código del modelo ("G502"): si la búsqueda lleva números,
   el título tiene que tenerlos (`query_match`).
4. **El resto del cupo**, repartido en partes iguales entre las búsquedas amplias y de especificación.
   Una publicación entra al grupo de una búsqueda si (`pool_match`) tiene la **primera palabra** (el tipo
   de producto, que no alcanza sola) y todos los **números** de la búsqueda ("4050"); si no hay números,
   al menos la mitad de las demás palabras. "RTX4050" pegado cuenta como "rtx 4050" y se saca la marca
   registrada pegada ("RyzenTM 7").
5. **Según el presupuesto** (`spread`): *económico* mira el 60 % más barato, *alto* el 60 % más caro, el
   resto todo el rango. Con tope en pesos *alto* se trata como *medio* (el tope ya acota).

Por qué reparto por precio y no "las más baratas": con presupuesto alto, las 80 más baratas eran todas
genéricas y la recomendación buena nunca se evaluaba.

## 4. Evaluación

`candidates.evaluate()`. Detalle del prompt, caché, tandas y reintentos en [AI.md](AI.md). En resumen:

- Tandas de `EVAL_BATCH` (30), `EVAL_PARALLEL` (5) a la vez.
- Cada publicación se responde como evaluada o `product:false`; lo que falta se pide de nuevo (tandas de
  `RETRY_CHUNK` = 10). Una tanda que la IA rechaza por larga se divide hasta `MIN_SPLIT` (8).
- **El código pone el tope, no la IA:** si el título contradice un obligatorio (`fails_must`), el puntaje no
  pasa de `FAIL_CAP` (40).
- **Equivalencias de gama** (i7 = Core Ultra 7 = Ryzen AI 7 = "Ryzen 7 o superior"; placa mayor cumple "o
  superior") están en el prompt.
- **Especificaciones de la ficha** (`search.vtex_specs`): memoria, pantalla, modelo exacto, peso, etc. de
  la ficha VTEX, además del título.
- **Caché de 6 h** por (perfil, título, ficha).

## 5. Disponibilidad en tu zona

`search.vtex_available()` + `candidates.drop_unavailable()`. La API de búsqueda de Frávega muestra con
stock publicaciones que en la web dicen *"Este producto no se encuentra disponible para tu ubicación"*.
Antes de mostrar un candidato (puntaje de "casi cumple" o más) se llama a la **simulación de compra** que usa
el carrito de la propia tienda (`POST /api/checkout/pub/orderForms/simulation`) con el código postal que la
persona puso en la página. Si `availability != "available"` o no hay opciones de entrega, se descarta y la
página lo cuenta ("No mostramos N publicaciones…"). Si no se puede saber (error de red), **no se descarta**.
También lo hace el escaneo de Seguir precio.

Límite conocido: no distingue publicaciones **importadas** ("Compra Internacional") de vendedores del
marketplace de Frávega.

## 6. Rangos y recomendación

- `group_by_model`: agrupa por `model_key` (el modelo normalizado por la IA); por modelo deja una oferta
  por tienda (la más barata), hasta 6, y marca `over_budget` si pasa el tope.
- `build_tiers`: de los que tienen puntaje ≥ `MIN_SCORE` (55), tres tramos **por precio y en proporción**
  (cortes a 1/3 y 2/3 del camino entre el mínimo y el máximo en escala logarítmica): dos modelos de precio
  parecido caen en el mismo tramo, y $20.000 contra $40.000 pesa lo mismo que $100.000 contra $200.000. Hasta
  `PER_TIER` (3) por tramo. Con pocos modelos (o precios casi iguales) hay un solo tramo.
- `recommend`: "Mi recomendación" es el mejor puntaje del tramo que va con el presupuesto (económico →
  económica, alto → premium, el resto → equilibrada); desempata el más barato.

## 7. Casi cumplen y cuánto cuesta completar

- **Casi cumplen** (`near_misses`): puntaje entre `NEAR_MIN` (25) y 54, hasta 6; primero los que se pueden
  completar. Cada uno muestra en qué falla ("No cumple: 32 GB de RAM").
- **Completar** (`core/upgrades.py`): si lo único que falta es RAM o disco **y** la IA indica que se puede
  ampliar (`fix`), se busca el precio real del módulo SODIMM (mismo DDR que la notebook) o del SSD M.2 en las
  tiendas: mediana de las 5 publicaciones más baratas que encajan, sin equipos completos ni kits. Se
  muestra el total contra la más barata que ya trae todo.
- **Confirmación** (`core/specs.py` + `candidates.confirm_upgrades`): la ficha del fabricante dice si de
  verdad se puede ampliar (RAM en ranuras o soldada, ranuras M.2). Si dice que la RAM está soldada, se saca
  la propuesta; si no se pudo confirmar, la tarjeta dice **"sin confirmar"**. Hoy siempre es "sin
  confirmar" porque la búsqueda de Google de Gemini no está en el plan gratis ([AI.md §9](AI.md#9-límites-conocidos)).

## 8. Mercado Libre

Ver [STORES.md §4](STORES.md#4-mercado-libre-vía-comparaya). En la búsqueda: los primeros `CY_MODELS` (5)
modelos sugeridos se consultan en ComparaYa (`CY_WORKERS` = 3 en paralelo, `CY_TRIES` = 4 direcciones por
modelo); sus ofertas de Mercado Libre y de tiendas que no leemos entran a la evaluación como cualquier
publicación.
