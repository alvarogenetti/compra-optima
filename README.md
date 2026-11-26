# Compra Óptima

Asesor de compras para Argentina. Contás qué querés comprar ("un mouse", "una secadora de pelo para pelo
largo"); una IA te hace unas preguntas simples sobre **cómo lo vas a usar**, arma tu **perfil de compra
explicado** (qué tiene que tener, qué es deseable, por qué no pagar de más), busca **precios reales** en tiendas
argentinas y te recomienda en tres rangos: económica, equilibrada y premium. Si querés, **sigue el precio** y te
avisa por Telegram cuando baja de verdad (contra el precio habitual, no contra el "precio de lista" inflado).

Nació del [tracker de monitores](https://github.com/alvarogenetti/monitor-precios-tracker): lo que allá se hizo a
mano (un perfil, requisitos, buscar en tiendas), acá lo hace una IA para cualquier producto.

> **Estado:** prueba con amigos y familia. Página: https://compra-optima.onrender.com (pide un código de acceso).

## Cómo funciona

```
Texto libre ──► Entrevista (IA) ──► Perfil explicado ──► Búsqueda real en las tiendas de esa categoría
                preguntas sobre        qué es / por qué        (VTEX, WooCommerce, Tiendanube + ML vía ComparaYa)
                el uso; "no sé" →      para vos                         │
                explica                                       Prefiltro ► IA evalúa ► disponible en tu zona
                                                                        │
              Resultados en 3 rangos ◄── agrupa por modelo ◄───────────┘
              + "casi cumplen" con cuánto cuesta completarlos
              + "Seguir precio" ──► Telegram cuando baja de verdad
```

## Uso

```powershell
.\iniciar.ps1                               # crea el entorno, copia .env.example a .env y abre http://127.0.0.1:8010
.venv\Scripts\python.exe -m pytest -q       # tests (sin red)
```

- **Con IA:** poné al menos una clave en `.env`: `GEMINI_API_KEY` (gratis en https://aistudio.google.com/apikey),
  y de respaldo `GROQ_API_KEY` y `OPENROUTER_API_KEY`. La app reparte el trabajo entre las que tengan clave.
- **Sin claves:** modo demo (preguntas fijas, evaluación por palabras): sirve para probar la interfaz.
- Variables, despliegue y qué hacer cuando algo falla: [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Documentación

Todo está documentado en [`docs/`](docs/README.md). Empezá por la arquitectura.

| Documento | Contenido |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Mapa del sistema, ciclo de una consulta, módulos, datos, despliegue, modos de falla |
| [AI.md](docs/AI.md) | Proveedores de IA, reparto de tareas, cuotas, **presupuesto de tokens medido**, **catálogo de prompts** |
| [SEARCH.md](docs/SEARCH.md) | Entrevista, búsqueda, prefiltro, evaluación, disponibilidad, rangos |
| [STORES.md](docs/STORES.md) | Tiendas, plataformas, Frávega, Mercado Libre vía ComparaYa |
| [FOLLOW.md](docs/FOLLOW.md) | Seguir precio, Telegram y escaneo programado |
| [OPERATIONS.md](docs/OPERATIONS.md) | Variables, despliegue, monitoreo, runbook |
| [DECISIONS.md](docs/DECISIONS.md) | Registro de decisiones de arquitectura |
| [PRUEBAS.md](docs/PRUEBAS.md) | Plan de pruebas manuales: qué probar y qué debería mostrar |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Cómo trabajar: convenciones, tests, documentación |

## Límites conocidos

- Las IA gratis tienen cupos chicos y Gemini es inestable (503): hay respaldo en cadena, pero una búsqueda grande
  puede tardar 1 a 3 minutos. Habilitar facturación en Google AI Studio lo resolvería de fondo.
- La ficha del fabricante (¿se puede ampliar la RAM?) necesita la búsqueda de Google de Gemini, que no viene en
  el plan gratis: hoy esas tarjetas dicen "sin confirmar".
- Mercado Libre sólo aparece cuando ComparaYa tiene el modelo. Frávega no distingue publicaciones importadas.
- Fuera de informática y gaming hay pocas tiendas: la calidad baja (ver [STORES.md](docs/STORES.md)).
- Render gratis se duerme tras 15 minutos sin visitas (la primera visita tarda ~1 minuto): un aviso cada 10
  minutos lo mantiene despierto, de mejor esfuerzo. Las búsquedas guardadas viven en el navegador de cada persona.

## Próximas etapas

1. Probar con amigos y familia y ajustar los prompts con lo que llegue por "¿Te sirvió?".
2. Seguir precio: historial en la web (gráfico como el del tracker), "avisame si baja de $X", eventos (Black
   Friday, Cyber Monday) con referencias congeladas y cuotas en pesos de hoy.
3. Más tiendas y categorías (belleza, deportes, hogar).
4. Habilitar facturación en Google AI Studio (estabilidad y fichas del fabricante) si se abre a más gente.
5. Monetización con links de afiliado, sin cobrar por destacar productos: la recomendación tiene que ser neutral.

## Estructura

```
app/        FastAPI (main.py) y la página (static/: index.html, app.js, styles.css)
core/       ai, usage, interview, candidates, search, comparaya, upgrades, specs, follow, watch, market,
            telegram, db, stores, guard, feedback, demo, settings, price_parser
config/     stores.json (tiendas y categorías) · ai_limits.json (límites de cada IA)
docs/       documentación de ingeniería
tests/      pytest sin red (IA y tiendas con dobles)
.github/    workflows: watch (escaneo cada 3 h) y tests
```
