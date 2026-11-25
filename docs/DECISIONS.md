# Decisiones de arquitectura (registro)

Cada decisión que condiciona el diseño, con su contexto, qué se descartó y qué consecuencias tiene.
Formato corto, en orden cronológico. **Una decisión nueva (o una revertida) se agrega o se actualiza acá
en el mismo commit que el cambio.** Estados: *vigente*, *reemplazada por ADR-n*.

---

### ADR-001 · Repo y proyecto separados del tracker de monitores — *vigente* (24/11/2026)
**Contexto.** El tracker de monitores corre solo en GitHub Actions y avisa por Telegram; es de una persona y
una categoría. Compra Óptima es multiusuario, de cualquier producto, con IA y servidor.
**Decisión.** Repo nuevo. Se **copian** los lectores y la lógica reutilizable (VTEX, WooCommerce, Tiendanube,
parser de precios, descuento real); no se comparte código por ahora.
**Consecuencias.** Un cambio de Compra Óptima no puede romper los avisos del tracker. Costo: hay lógica
duplicada (ej. Frávega con IVA se corrigió en los dos). Se revisa si conviene una librería común cuando la
duplicación duela.

### ADR-002 · Render (web) y no GitHub Pages; código de acceso y límite por persona — *vigente* (24/11/2026)
**Contexto.** La app necesita servidor (Python, claves de IA, consultas a tiendas). GitHub Pages sólo sirve
archivos estáticos.
**Decisión.** Render, plan gratis, desplegado desde `render.yaml`. Como las IA gratis tienen cupo chico,
`ACCESS_CODE` + límite por IP y hora para que nadie de afuera lo gaste.
**Consecuencias.** El servidor se duerme tras 15 min (primera visita ~1 min). Aceptable para una prueba con
amigos y familia; se revisa al abrirlo a más gente.

### ADR-003 · Sin estado de sesión en el servidor — *vigente* (24/11/2026)
**Decisión.** La conversación y el perfil los guarda el navegador y viajan completos en cada pedido.
**Consecuencias.** Despliegue y escala simples, nada que perder al reiniciar. A cambio, el costo de
tokens de la entrevista crece con los turnos (mitigado en ADR-012).

### ADR-004 · IA gratis detrás de una capa intercambiable — *vigente* (24/11/2026, ampliada 25/11/2026)
**Contexto.** Es una prueba con amigos y familia; no hay presupuesto para IA. Los planes gratis tienen
límites chicos y son inestables.
**Decisión.** Todo pasa por `core/ai.py` (`get_ai().json(...)`). Primero Gemini (con lista de modelos y
pausas), después Groq y OpenRouter en cadena (ADR-011). Modo demo sin clave.
**Descartado.** Cerebras como IA gratis: desde julio de 2026 es prueba de $5 y luego pago.
**Consecuencias.** Cambiar de IA es agregar una clase. Los planes gratis pueden usar los datos para
entrenar: se avisa. Si se abre a más gente, el paso natural es habilitar facturación.

### ADR-005 · Mercado Libre sólo vía fichas públicas de ComparaYa — *vigente* (24/11/2026)
**Contexto.** Mercado Libre es central en Argentina, pero sus términos prohíben robots y scraping, y su API
bloqueó (23/11/2026) a las apps que sólo leen precios. Se propuso Jina Reader.
**Decisión.** No se lee ML directo ni por intermediarios que lo scrapean (Jina Reader, proxies, Apify). Se
leen las fichas de **ComparaYa**, un comparador afiliado de ML cuyo `robots.txt` permite `/p/...` y prohíbe
`/api/`; nunca su buscador.
**Consecuencias.** Cobertura parcial (sólo modelos que ComparaYa tiene; precios con horas de atraso); queda
el link de búsqueda en ML. Se respeta la regla de no evadir controles. Ver [STORES.md](STORES.md).

### ADR-006 · Tiendas por plataforma, registradas en JSON y etiquetadas por categoría — *vigente* (24/11/2026)
**Decisión.** `config/stores.json`: cada tienda con su plataforma (VTEX, WooCommerce, Tiendanube) y las
categorías que vende. Sólo se busca en las de la categoría del perfil.
**Consecuencias.** Sumar una tienda es una línea. Cobertura floja fuera de informática y gaming (pocas
generalistas): límite conocido.

### ADR-007 · Descuento "real" contra la mediana de 30 días — *vigente* (24/11/2026, heredada del tracker)
**Contexto.** El "precio de lista" tachado de las tiendas se infla antes de los eventos.
**Decisión.** Referencia = mediana del mejor precio de cada escaneo en 30 días (sin las últimas 24 h);
buena oferta desde 15 %, excepcional desde 30 %, y se marca el descuento inflado. Hasta tener historia, el
precio de partida.
**Consecuencias.** Hace falta tiempo para tener historia; durante las primeras semanas la referencia es el
precio inicial.

### ADR-008 · Avisos por Telegram con bot propio; Neon para los datos — *vigente* (24/11/2026)
**Decisión.** Telegram (link con `/start <token>`, sin cuentas) y no email; Postgres de Neon (gratis, no
vence) porque Render gratis no guarda archivos. Bot **propio**, no el del tracker (el webhook se le pisaría).
**Consecuencias.** Datos personales mínimos (`chat_id`). El escaneo corre en GitHub Actions y comparte la
base con la web.

### ADR-009 · Verificar que se pueda comprar antes de recomendar — *vigente* (25/11/2026)
**Contexto.** Frávega muestra con stock en su API publicaciones que en la web dicen "no disponible para tu
ubicación".
**Decisión.** Simulación de compra de VTEX con el código postal de la persona; se descarta lo que no se
puede entregar. Si no se puede saber, no se descarta.
**Consecuencias.** Una consulta extra por candidato mostrable. No resuelve las "Compra Internacional".

### ADR-010 · La IA no inventa: lo sugerido se verifica y lo no confirmado se marca — *vigente* (24/11/2026)
**Decisión.** Los modelos que sugiere la IA sólo se recomiendan si aparecen en una tienda. La ficha del
fabricante se consulta con búsqueda de Google (con fuentes) y, si no se confirma, la tarjeta dice "sin
confirmar". El tope de puntaje por requisito incumplido lo aplica el código, no la IA.
**Consecuencias.** Menos recomendaciones espectaculares, más confiables. La ficha del fabricante está apagada
mientras la búsqueda de Google no esté en el plan.

### ADR-011 · Reparto de tareas por ruta, con cuota y salud — *vigente* (25/11/2026)
**Contexto.** Medición real: Gemini gratis dio 503/429 seguido; Groq es rápido pero sólo 8.000 tokens/min;
un modelo chico armó mal un perfil.
**Decisión.** `ChainAI` con ruta por tarea (preguntas → Groq; perfil y evaluación → Gemini), admisión por
cuota **antes** de consultar (`core/usage.py`, límites en `config/ai_limits.json`), la IA que falla pasa al
final y respaldo en cadena. Ver [AI.md](AI.md) §3 y §4.
**Descartado.** Rotar claves o proyectos de una misma IA para esquivar el límite (viola sus términos);
balanceo por turnos sin considerar tarea (mandaría el perfil a un modelo chico).
**Consecuencias.** El orden por defecto depende de mediciones de un día; se revisa con `/api/usage`.

### ADR-012 · Ahorro de tokens: prompt en dos partes, caché de evaluaciones y completitud verificada — *vigente* (25/11/2026)
**Contexto.** Medición: el prompt de la entrevista pesaba ~2.600 tokens en cada turno (54 % era la parte del
perfil, inútil en los primeros 3); la evaluación a veces devolvía 2 de 30 publicaciones sin avisar.
**Decisión.** Prompt corto mientras no se puede cerrar; caché de evaluaciones por (perfil, título, ficha)
con TTL 6 h; una respuesta por publicación (`product:false` para las descartadas) y reintento sólo de lo
que falte; salida mínima para puntajes bajos. Ver [AI.md](AI.md) §6.
**Consecuencias.** −35 % de tokens en la entrevista y −62 % en una búsqueda repetida, medidos. La salida de
una búsqueda en frío creció (se paga completo lo que antes se perdía) y se encuentran más modelos. La caché
es por proceso (en memoria).

### ADR-013 · Evaluación en tandas, divisibles y en paralelo — *vigente* (25/11/2026)
**Contexto.** Con 120 publicaciones juntas la IA devolvía 4; Groq rechaza respuestas largas.
**Decisión.** Tandas de 30, 5 en paralelo; si una IA rechaza una tanda se divide en mitades hasta 8.
**Consecuencias.** Más consultas pero más completas y más rápidas.

### ADR-014 · Tiendas que bloquean a la nube se marcan y no se consultan allí — *vigente* (25/11/2026)
**Decisión.** `cloud_blocked: true` (Comeros, DeCaba, Hardcore: 403 desde servidores). No se consultan desde
Render ni GitHub Actions; sí desde una PC. No se intenta evadir el bloqueo.
**Consecuencias.** Menos ruido en "Dónde buscamos"; menos cobertura en lo publicado.
