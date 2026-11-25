# Seguir precio: avisos por Telegram cuando un precio baja de verdad

Módulos: `core/follow.py`, `core/telegram.py`, `core/watch.py`, `core/market.py`, `core/db.py`.
Workflow: `.github/workflows/watch.yml`. **No usa IA.**

- [1. Experiencia de la persona](#1-experiencia-de-la-persona)
- [2. Cómo funciona](#2-cómo-funciona)
- [3. Qué es "bajar de verdad"](#3-qué-es-bajar-de-verdad)
- [4. Reglas anti-ruido](#4-reglas-anti-ruido)
- [5. Activación](#5-activación)

## 1. Experiencia de la persona

En un resultado: **🔔 Seguir precio** → **Abrir Telegram** → **Iniciar**. Sin cuentas ni contraseñas. El bot
confirma el modelo y el precio de hoy. En el bot: `/lista` (lo que seguís) y `/dejar_<n>` (dejar de seguir).
Máximo `MAX_FOLLOWS_PER_CHAT` (20) por persona.

## 2. Cómo funciona

```
Web: POST /api/follow ──► follows (token, sin dueño) ──► link t.me/<bot>?start=<token>
Persona toca Iniciar ──► Telegram ──► webhook /api/telegram/<clave> ──► follow.handle ──► follows.watcher_id
GitHub Actions (cada 3 h) ──► watch.run ──► busca cada modelo UNA vez ──► prices ──► avisa por Telegram
```

- **Vínculo.** La web crea el seguimiento sin dueño con un token aleatorio. El `/start <token>` lo asigna
  al chat. Si alguien comparte su link, quien lo abre obtiene su **propio** seguimiento. Los seguimientos
  que nadie vinculó en 7 días se borran (`UNLINKED_TTL_DAYS`).
- **Webhook.** Se registra solo cuando la web arranca en Render (`RENDER_EXTERNAL_URL`). La ruta lleva una
  clave derivada del token del bot (`telegram.webhook_secret`), sin otra variable. Otra ruta responde 404.
- **Escaneo.** `watch.run`: agrupa los seguimientos por modelo (un modelo que siguen 50 personas se busca
  **una vez**), lo busca en las tiendas de su categoría, y **sólo cuenta** una publicación si el título
  tiene todas las palabras del modelo salvo la marca y no es un accesorio (`watch.matches`: "G502 Hero" no
  pasa por "G502 Lightspeed"; "Cable USB para G502" tampoco). Guarda el más barato de cada tienda en
  `prices`. Suma Mercado Libre vía ComparaYa (sin importados). Descarta publicaciones que la tienda no puede
  entregar (simulación de compra).
- **Una base compartida.** La web (Render) y el escaneo (Actions) usan la misma Postgres de Neon.

## 3. Qué es "bajar de verdad"

`core/market.py` (traído del tracker de monitores):

- **Precio habitual** = mediana del mejor precio de cada escaneo de los últimos 30 días
  (`WINDOW_DAYS`), sin contar las últimas 24 h (así una baja de hoy se compara contra "antes"). Hace falta
  historia: al menos `MIN_POINTS` (6) escaneos repartidos en `MIN_SPAN_DAYS` (2) días. Hasta entonces, el
  precio de cuando la persona empezó a seguirlo.
- **Descuento real** = (habitual − precio) / habitual. **≥ 15 %** (`GOOD`) es "buena oferta" 💰; **≥ 30 %**
  (`GREAT`), "excepcional" 🔥.
- **Descuento inflado:** si la tienda anuncia −20 % o más pero contra el mercado es menos de la mitad, el
  aviso lo dice. El "precio de lista" tachado de las tiendas no es una referencia confiable.
- **Primer escaneo de cada seguimiento:** no avisa ofertas, **fija el punto de partida real**. La web pudo
  haber mostrado otra publicación del mismo modelo (caso real: MSI PRO MP241 en Frávega a $474.962 y otra
  publicación a $293.716). Si el escaneo lo encuentra 15 % o más barato que lo que se vio, lo cuenta como
  **dato** ("es otra publicación, no una baja") y toma ese precio como partida.

## 4. Reglas anti-ruido

- La misma oferta no se repite: sólo si mejora un `REALERT_IMPROVEMENT` (3 %) o pasó `REALERT_DAYS` (7).
- Un modelo que falla en el escaneo no corta a los demás.
- Un chat que bloqueó al bot no corta los demás avisos (`telegram.send` devuelve `False`).
- Sin `COMPRA_BOT_TOKEN` o sin base, el botón queda apagado (`/api/status` → `follow_enabled: false`).

## 5. Activación

Una vez: bot nuevo en @BotFather (**no el del tracker**: se le registraría el webhook), proyecto en Neon,
y las variables `COMPRA_BOT_TOKEN` y `DATABASE_URL` en Render **y** como secrets del repo (el escaneo las
usa). Pasos y troubleshooting en [OPERATIONS.md](OPERATIONS.md).

GitHub desactiva los workflows programados de un repo público sin actividad en 60 días; se reactivan desde
la pestaña Actions. Si faltan los secrets, el workflow lo informa y termina sin error.
