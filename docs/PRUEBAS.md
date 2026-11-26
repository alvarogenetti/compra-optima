# Plan de pruebas manuales

Para probar la app como lo haría una persona real y devolver qué anduvo y qué no. Cada caso dice **qué escribir**,
**qué debería mostrar** y **qué sería una señal de problema**. Los números (tiempos, precios) salen de las
mediciones del 25 y 26/11/2026: son un orden de magnitud, no una garantía.

- [Antes de empezar](#antes-de-empezar)
- [Qué mirar siempre](#qué-mirar-siempre)
- [A. La entrevista](#a-la-entrevista)
- [B. La búsqueda y los resultados](#b-la-búsqueda-y-los-resultados)
- [C. Funciones extra](#c-funciones-extra)
- [D. Celular, acceso y resiliencia](#d-celular-acceso-y-resiliencia)
- [Cómo devolver lo que viste](#cómo-devolver-lo-que-viste)

## Antes de empezar

- Link: https://compra-optima.onrender.com (pide el código de acceso). Si tarda más de 15 segundos en abrir, es el
  arranque en frío de Render (ver [OPERATIONS.md](OPERATIONS.md)): anotalo.
- Para "Seguir precio" necesitás Telegram en el celular o en la compu.
- Una búsqueda completa usa cupo de IA gratis: **no hagas más de 12 búsquedas por hora** (es el límite por persona).
  Si te aparece "Llegamos al límite gratis de la IA", es esperable: anotá a qué hora fue.
- Empezá en una ventana nueva; los resultados viejos están en "Tus búsquedas anteriores".

## Qué mirar siempre

| Qué | Cómo debería ser |
|---|---|
| Tiempo hasta la **primera pregunta** | 2 a 5 segundos (hasta ~20 si la IA está lenta) |
| Tiempo entre preguntas | 1 a 5 segundos |
| Tiempo de una **búsqueda** | 40 segundos a 2 minutos; hasta 3 si Gemini está inestable. Tiene que mostrar en qué paso va |
| **Precios** | los de la tienda: abrí 2 links por búsqueda y compará (Frávega tiene que coincidir con la web) |
| **Links** | abren la ficha del producto, no una página vacía ni "no existe" |
| **Texto** | en español rioplatense, sin tecnicismos sin explicar, sin frases cortadas ni en inglés |
| **Errores** | mensajes claros que dicen qué hacer; nada de códigos ni pantallas en blanco |

## A. La entrevista

| # | Qué escribís | Qué debería mostrar | Señales de problema |
|---|---|---|---|
| A1 | `un mouse para jugar` | De 3 a 6 preguntas **sobre cómo lo usás** (tipo de juegos, mano, cable o inalámbrico, ruido, presupuesto), con 2 a 5 opciones cada una. Panel "Lo que sé hasta ahora" que se va llenando. Al responder, las opciones desaparecen hasta la pregunta siguiente | pregunta cosas técnicas sin explicar (DPI, switches); repite una pregunta; las opciones quedan visibles tras responder |
| A2 | `quiero un monitor` (nada más) | De 5 a 10 preguntas que cubren **uso, tamaño y resolución (Full HD / 2K / 4K con lo que cambia cada una), fluidez (Hz), plano o curvo, con qué lo conectás, marca y presupuesto** | termina en 3 preguntas; nunca pregunta resolución o marca |
| A3 | El pedido largo: `buena notebook de RTX 4050 para arriba, con al menos 1T de espacio, buenos hz de 100 para arriba, de Ryzen 7 para arriba, entre 1.000.000 y 3.000.000, 32GB de Ram para arriba` | **Igual hace al menos 3 preguntas** (uso, tamaño de pantalla o portabilidad, marca). El perfil conserva **tal cual** lo que pediste: "RTX 4050 o superior", "32 GB de RAM o más", "1 TB o más", "Ryzen 7 o superior", "100 Hz o más" | salta directo al perfil sin preguntar; el perfil resume o pierde requisitos ("GPU", "RAM" sin números) |
| A4 | En cualquier pregunta, tocá **"No sé, explicame"** | Una explicación corta con ejemplos de la vida real (💡) y **la misma pregunta de nuevo**, más simple | explica con jerga; no repite la pregunta; cuenta como pregunta nueva en el contador |
| A5 | Después de 2 o 3 preguntas, tocá **"Ya está, mostrame el resultado"** | Arma el perfil con lo que sabe | el botón no aparece o no hace nada |
| A6 | `hola, ¿cómo estás?` | Explica amablemente qué hace y pregunta qué querés comprar | se rompe, inventa un producto o sigue como si fuera una compra |
| A7 | `algo para regalarle a mi mamá` | Hace preguntas para entender (qué le gusta, presupuesto) o pregunta qué tipo de producto | falla con un error; dice una categoría al azar |
| A8 | Ya en el perfil: **"Corregir algo"** y escribí `mi tope es $80.000` | Vuelve al chat y arma un perfil nuevo con ese tope. En el inicio aparecen **las dos** búsquedas | pisa la anterior; ignora el cambio |

**Mirá el perfil (A1 a A3):** tres grupos: *Tiene que tener*, *Deseable* y *No pagues extra por*. Cada característica
con **"Qué es"** (entendible por cualquiera) y **"Por qué para vos"** (citando lo que dijiste). El presupuesto
coincide con lo que dijiste.

## B. La búsqueda y los resultados

Escribí un código postal real (ej. `3000`) en la pantalla del perfil antes de **Buscar precios**.

| # | Qué buscás | Qué debería mostrar | Señales de problema |
|---|---|---|---|
| B1 | **Progreso**: cualquier búsqueda | Una tarjeta con 5 pasos (tiendas → Mercado Libre y otras → evaluación con "tanda N de 5" → disponibilidad → armado), los hechos con ✓, el actual girando, y el tiempo transcurrido | solo una ruedita; el tiempo no avanza; se queda en el mismo paso más de 2 minutos |
| B2 | `un mouse inalámbrico con muchos botones, pago más si vale la pena` | Resultados en filas (**Económica / Equilibrada / Premium**), hasta 3 modelos por fila, una con **"Mi recomendación"**. Modelos conocidos (Logitech, Razer, Corsair…) a precios de decenas o cientos de miles de pesos | modelos que no son mouses; accesorios (pads, cables); solo opciones baratas con presupuesto alto |
| B3 | **El pedido largo de A3** (notebook) | Pocos modelos que cumplan todo, y una sección **"Casi cumplen"** con en qué fallan ("No cumple: 32 GB de RAM") y, si se puede ampliar, **"Cómo completarla"** con el precio de la RAM/SSD y el total contra la que ya trae todo. Debería aparecer la ASUS TUF FA607NUQ de Frávega o similares | una Ryzen 5 marcada como que cumple "Ryzen 7"; publicaciones "no disponibles en tu zona"; "Casi cumplen" vacío con notebooks de $2,5 a 3 M |
| B4 | `una secadora de pelo para pelo largo y grueso` | Secadores reales (Philips, Gama, Remington…) en un rango de decenas de miles de pesos (el 25/11 salieron entre ~$43.000 y ~$110.000). Menos tiendas que en informática | resultados de "secadores de ropa", "secadores de goma" o accesorios |
| B5 | `una freidora de aire para dos personas` | Freidoras de aire de capacidad chica o mediana | hornos o ollas; solo modelos enormes |
| B6 | `un colchón de dos plazas` | Puede traer **pocos** resultados (hay pocas tiendas de hogar). Lo importante: **no inventa** y lo dice con claridad ("entre lo que revisamos…") | modelos inventados; mensaje confuso; error |
| B7 | Mirá **cada tarjeta** | Modelo, **% de encaje**, precio con la tienda, cuotas sin interés si hay, una razón en una oración, ✓ lo que cumple, **?** lo que no se pudo confirmar, y botones *Ver en…*, *Seguir precio*, *¿Y en Mercado Libre?* | precio en $0 o absurdo; razón que no tiene que ver con el producto; botones que no hacen nada |
| B8 | Al final de la página | "Mirá también en Mercado Libre" con links; **"¿Te sirvió?"**; **"Dónde buscamos"** con las tiendas que respondieron (en informática, 18 más una fila de ComparaYa) | "Dónde buscamos" con casi todas en rojo |
| B9 | Buscá con y sin **código postal** | Con código postal, un aviso "No mostramos N publicaciones que la tienda tiene sin stock para tu código postal" (si hay) | un producto que, al abrirlo, dice "no disponible para tu ubicación" |

## C. Funciones extra

| # | Qué hacés | Qué debería mostrar | Señales de problema |
|---|---|---|---|
| C1 | **Historial.** Terminá una búsqueda y tocá "Empezar de nuevo" (o el logo) | Volvés al **inicio** (siempre). Abajo, **"Tus búsquedas anteriores"** con la búsqueda, la fecha y tu recomendación. Al tocarla, se reabre con **"Búsqueda del … (hace N min)"** | te lleva a un resultado viejo al abrir el link; la búsqueda no queda guardada |
| C2 | En una búsqueda guardada vieja, tocá **"Actualizar precios"** | Repite la búsqueda con el mismo perfil (más rápida, por la caché) y actualiza la fecha | no hace nada; pierde la búsqueda anterior |
| C3 | Recargá la página (F5) | Abre en el inicio; el historial sigue ahí | pierde el historial |
| C4 | **"Borrar"** en una búsqueda guardada | Desaparece | vuelve a aparecer |
| C5 | **"¿Y en Mercado Libre?"** en un modelo conocido (ej. Logitech G502 Lightspeed) | El precio de ML, cuánto más o menos que la tienda, y "Otras tiendas que no revisamos" | cuelga; precios de ML que no existen |
| C6 | Lo mismo en un modelo poco común | "No encontramos este modelo en ComparaYa" + link a buscar en ML | error |
| C7 | **Seguir precio**: 🔔 en una tarjeta → "Abrir Telegram" → **Iniciar** | El bot responde **"Listo ✅ Sigo … por vos. Hoy: $…"** con el precio. `/lista` muestra el producto y `/dejar_N` lo saca | el bot no contesta; el botón sigue gris (Seguir precio apagado) |
| C8 | Tocá "Seguir precio" en **dos** modelos distintos | Ambos aparecen en `/lista` | uno pisa al otro |
| C9 | **"¿Te sirvió?"**: elegí 👍/👎 y escribí algo | "¡Gracias por contarnos!" y el comentario le llega por Telegram al dueño (si están cargados `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en Render; si no, queda en los Logs de Render) | error al enviar |

## D. Celular, acceso y resiliencia

| # | Qué hacés | Qué debería mostrar | Señales de problema |
|---|---|---|---|
| D1 | Todo el flujo (A1 + B2) **desde el celular** | Sin scroll horizontal, botones fáciles de tocar, tarjetas una debajo de la otra, el teclado no tapa el campo de respuesta | texto cortado; botones muy chicos; la página se desplaza hacia los costados |
| D2 | Abrí el link **sin código guardado** (ventana privada) | Pantalla "Prueba privada". Con un código malo: "Código de acceso incorrecto". Con el bueno, entra | deja usar la IA sin código |
| D3 | Entrá después de **más de 20 minutos** sin usarla | Abre rápido (el aviso cada 10 minutos la mantiene despierta). Si tarda ~1 minuto, anotá cuánto esperaste | tarda siempre |
| D4 | Si ves **"Llegamos al límite gratis de la IA"** o **"La IA de Google está saturada"** | El mensaje dice **en cuánto volver a probar**; al reintentar, funciona | mensaje técnico; el reintento no sirve |
| D5 | Lanzá **dos búsquedas a la vez** (otra pestaña) | Las dos terminan (hasta 3 a la vez). Una cuarta dice "Hay varias búsquedas en curso" | se mezclan los resultados |
| D6 | Dejá una búsqueda corriendo y **cerrá la pestaña** | Al volver a abrir, el inicio; la búsqueda no se guardó (no terminó) | — (es lo esperado: una búsqueda a medias no se guarda) |

## Cómo devolver lo que viste

Para cada caso que pruebes, mandame (en cualquier formato; una captura alcanza):

1. **El número del caso** (A3, B4…) y **qué escribiste** (copiado).
2. **Qué pasó** frente a "qué debería mostrar": ✅ bien, ⚠️ raro, ❌ mal.
3. **Cuánto tardó** (la primera pregunta y la búsqueda) y **a qué hora**, para cruzarlo con el consumo de IA.
4. Si algo no te cerró: **una captura** y qué esperabas.

Con eso reviso el consumo y los registros del servidor (`/api/usage`, Logs de Render) de ese rato, y te devuelvo un
resumen con lo que se repite, lo que arreglaría primero y lo que conviene dejar para después.
