"use strict";
/* Compra Óptima — lógica de la página. Sin frameworks: 4 vistas (inicio, entrevista, perfil, resultados).
   El estado vive acá y se guarda en el navegador para no perderlo al recargar. */

const $ = (s) => document.querySelector(s);
const state = { conversation: [], last: null, profile: null, results: null, view: "home", adjusting: false };
let status = { stores: 0, provider: "" };
let busy = false;
const STORE_KEY = "compra-optima:v1";
const money = new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS", maximumFractionDigits: 0 });

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function save() { try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (_) { /* sin almacenamiento */ } }
function load() {
  try { const s = JSON.parse(localStorage.getItem(STORE_KEY) || "null"); if (s) Object.assign(state, s); } catch (_) { /* nada */ }
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 7000);
}

const CODE_KEY = "compra-optima:code";
function getCode() { try { return localStorage.getItem(CODE_KEY) || ""; } catch (_) { return ""; } }
function setCode(c) { try { if (c) localStorage.setItem(CODE_KEY, c); else localStorage.removeItem(CODE_KEY); } catch (_) { /* nada */ } }

async function api(path, body, code = getCode()) {
  const headers = { "Content-Type": "application/json" };
  if (code) headers["X-Access-Code"] = code;
  const r = await fetch(path, body !== undefined ? { method: "POST", headers, body: JSON.stringify(body) } : { headers });
  let data = null;
  try { data = await r.json(); } catch (_) { /* respuesta vacía */ }
  if (r.status === 401 && data && data.code_required && path !== "/api/check-code") {
    setCode("");
    showCode();
  }
  if (!r.ok) throw new Error((data && (data.error || (data.detail && JSON.stringify(data.detail)))) || `Error ${r.status}`);
  return data;
}

let viewAfterCode = "home";
function showCode() {
  if (state.view !== "code") viewAfterCode = state.view || "home";
  for (const v of ["code", "home", "chat", "profile", "results"]) $(`#view-${v}`).hidden = v !== "code";
  state.view = "code";
  $("#restart-btn").hidden = true;
  setTimeout(() => $("#code-input").focus(), 50);
}

function show(view) {
  state.view = view;
  for (const v of ["code", "home", "chat", "profile", "results"]) $(`#view-${v}`).hidden = v !== view;
  $("#restart-btn").hidden = view === "home";
  save();
  window.scrollTo({ top: 0 });
}

/* ---------------- Inicio ---------------- */
function start(text) {
  text = text.trim();
  if (!text) { $("#start-input").focus(); return; }
  Object.assign(state, { conversation: [{ role: "user", content: text }], last: null, profile: null, results: null, adjusting: false });
  show("chat");
  renderThread();
  ask();
}

/* ---------------- Entrevista ---------------- */
function aiTurns() {
  return state.conversation.filter((m) => m.role === "assistant").map((m) => { try { return JSON.parse(m.content); } catch (_) { return null; } });
}

async function ask(finish = false) {
  if (busy) return;
  busy = true;
  setComposer(false);
  const typing = document.createElement("div");
  typing.className = "msg-ai typing"; typing.innerHTML = "<i></i><i></i><i></i>";
  $("#thread").appendChild(typing);
  typing.scrollIntoView({ block: "end", behavior: "smooth" });
  try {
    const out = await api("/api/interview", { conversation: state.conversation, finish });
    const { asked, max_questions, ...turn } = out;
    state.conversation.push({ role: "assistant", content: JSON.stringify(turn) });
    state.last = out;
    state.adjusting = false;
    if (out.type === "profile") {
      state.profile = out.profile;
      state.results = null;
      renderProfile();
      show("profile");
    } else {
      renderThread();
    }
  } catch (e) {
    toast(e.message);
    // Se saca la última respuesta del usuario para poder reintentar
    const lastMsg = state.conversation[state.conversation.length - 1];
    if (state.conversation.length === 1) {
      // Falló la primera pregunta: volvemos al inicio con el texto para reintentar
      $("#start-input").value = lastMsg.content;
      state.conversation = [];
      show("home");
    } else if (lastMsg && lastMsg.role === "user") {
      state.conversation.pop();
      $("#answer-input").value = lastMsg.content;
    }
    renderThread();
  } finally {
    typing.remove();
    busy = false;
    setComposer(true);
    renderOptions();   // vuelve a armar las opciones (el botón "Listo" arranca deshabilitado)
    save();
  }
}

function answer(text) {
  text = String(text || "").trim();
  if (!text || busy) return;
  state.conversation.push({ role: "user", content: text });
  $("#answer-input").value = "";
  renderThread();
  ask();
}

function setComposer(enabled) {
  for (const el of $("#composer").querySelectorAll("button, input")) el.disabled = !enabled;
}

function renderThread() {
  const thread = $("#thread");
  thread.innerHTML = "";
  for (const m of state.conversation) {
    if (m.role === "user") {
      const d = document.createElement("div");
      d.className = "msg-user"; d.textContent = m.content;
      thread.appendChild(d);
      continue;
    }
    let t; try { t = JSON.parse(m.content); } catch (_) { continue; }
    const d = document.createElement("div");
    d.className = "msg-ai";
    if (t.type === "profile") {
      d.innerHTML = `<div class="q">Listo, armé tu perfil de compra.</div><div class="help">${esc(t.profile?.product || "")} — lo podés ver y corregir.</div>`;
    } else {
      d.innerHTML = (t.explanation ? `<div class="explain">${esc(t.explanation)}</div>` : "") +
        `<div class="q">${esc(t.question)}</div>` + (t.help ? `<div class="help">${esc(t.help)}</div>` : "");
    }
    thread.appendChild(d);
  }
  if (state.adjusting) {
    const d = document.createElement("div");
    d.className = "msg-ai";
    d.innerHTML = `<div class="q">¿Qué querés corregir?</div><div class="help">Escribilo como te salga: "en realidad lo uso para viajar", "mi tope es $80.000"…</div>`;
    thread.appendChild(d);
  }
  renderOptions();
  renderDraft();
  renderProgress();
  const last = thread.lastElementChild;
  if (last) last.scrollIntoView({ block: "end" });
}

function renderOptions() {
  const box = $("#options");
  box.innerHTML = "";
  const t = state.last;
  if (!t || t.type !== "question" || state.adjusting) return;
  // Si lo último es una respuesta del usuario, esa pregunta ya se contestó: sin opciones hasta la próxima
  const lastMsg = state.conversation[state.conversation.length - 1];
  if (!lastMsg || lastMsg.role === "user") return;
  const picked = new Set();
  for (const opt of t.options || []) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "option"; b.textContent = opt;
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", () => {
      if (/escrib/i.test(opt)) { $("#answer-input").focus(); $("#answer-input").placeholder = "Escribilo acá…"; return; }
      if (!t.multi) { answer(opt); return; }
      if (picked.has(opt)) picked.delete(opt); else picked.add(opt);
      b.setAttribute("aria-pressed", String(picked.has(opt)));
      done.disabled = picked.size === 0;
    });
    box.appendChild(b);
  }
  const done = document.createElement("button");
  if (t.multi) {
    done.type = "button"; done.className = "btn btn-primary"; done.textContent = "Listo"; done.disabled = true;
    done.addEventListener("click", () => answer([...picked].join(", ")));
    box.appendChild(done);
  }
}

function renderDraft() {
  const dl = $("#draft");
  const draft = (state.last && state.last.draft) || [];
  dl.innerHTML = draft.length
    ? draft.map((d) => `<div><dt>${esc(d.label)}</dt><dd>${esc(d.value)}</dd></div>`).join("")
    : `<div class="empty">A medida que respondas, acá se va armando lo que buscás.</div>`;
}

function renderProgress() {
  const asked = aiTurns().filter((t) => t && t.type === "question" && !t.explanation).length;
  const max = (state.last && state.last.max_questions) || 8;
  $("#progress").innerHTML = state.adjusting ? "Corrigiendo el perfil" :
    `Pregunta ${Math.max(asked, 1)} · suelen ser entre 4 y ${max}<div class="progress-bar"><span style="width:${Math.min(100, asked / max * 100)}%"></span></div>`;
  $("#finish-btn").hidden = asked < 2 || state.adjusting;
  $("#explain-btn").hidden = state.adjusting;
  $("#answer-input").placeholder = state.adjusting ? "Contame qué cambiar…" : "O escribí tu respuesta…";
}

/* ---------------- Perfil ---------------- */
function featureList(items, kind) {
  if (!items || !items.length) return "";
  return items.map((f) => `<article class="feature ${kind === "avoid" ? "avoid" : ""}">
      <h3>${esc(f.feature)}</h3>
      ${f.what_is ? `<p class="what"><b>Qué es:</b> ${esc(f.what_is)}</p>` : ""}
      ${f.why ? `<p class="why"><b>${kind === "avoid" ? "Por qué no" : "Por qué para vos"}:</b> ${esc(f.why)}</p>` : ""}
    </article>`).join("");
}

function renderProfile() {
  const p = state.profile;
  if (!p) return;
  const b = p.budget || {};
  const levels = { economico: "Lo más barato que sirva bien", medio: "Precio medio", alto: "Pagás más si vale la pena", sin_definir: "Sin definir" };
  const group = (title, sub, items, kind) => items && items.length
    ? `<section class="feature-group"><h2>${title}</h2><p class="sub">${sub}</p><div class="features">${featureList(items, kind)}</div></section>` : "";
  $("#profile").innerHTML = `
    <div class="eyebrow">Tu perfil de compra</div>
    <h1>${esc(p.product)}</h1>
    ${p.summary ? `<p class="summary">${esc(p.summary)}</p>` : ""}
    ${group("✅ Tiene que tener", "Lo que sí o sí necesitás para tu uso.", p.must)}
    ${group("⭐ Deseable", "Suma si está, pero no es imprescindible.", p.nice)}
    ${group("🚫 No pagues extra por", "Cosas que suben el precio y no vas a aprovechar.", p.avoid_paying_for, "avoid")}
    <div class="budget-note"><b>Presupuesto:</b> ${esc(levels[b.level] || levels.sin_definir)}${b.max_ars ? ` · tope ${money.format(b.max_ars)}` : ""}${b.note ? ` — ${esc(b.note)}` : ""}</div>`;
  $("#search-btn").textContent = status.stores ? `Buscar precios en las tiendas` : "Buscar precios";
}

/* ---------------- Resultados ---------------- */
async function search() {
  if (busy || !state.profile) return;
  busy = true;
  show("results");
  $("#results").innerHTML = `<div class="loading"><div class="spinner"></div>
    <p><b>Buscando ${esc(state.profile.product.toLowerCase())} en las tiendas…</b></p>
    <p>Consultamos precios reales y después evaluamos cada publicación contra tu perfil. Puede tardar hasta un minuto.</p></div>`;
  try {
    state.results = await api("/api/candidates", { profile: state.profile });
    state.feedbackSent = false;
    renderResults();
  } catch (e) {
    $("#results").innerHTML = `<div class="empty-state"><h2>No pudimos terminar la búsqueda</h2><p>${esc(e.message)}</p>
      <p><button class="btn" id="retry-btn">Reintentar</button></p></div>`;
    $("#retry-btn").addEventListener("click", () => { busy = false; search(); });
  } finally {
    busy = false;
    save();
  }
}

function card(c, recommended) {
  const best = c.best;
  const sub = [];
  if (best.list) sub.push(`<s>${money.format(best.list)}</s>`);
  if (best.cuotas) sub.push(`${best.cuotas} cuotas sin interés`);
  if (best.promo && !best.cuotas) sub.push(esc(best.promo));
  const others = c.offers.slice(1);
  return `<article class="card ${recommended ? "recommended" : ""}">
    ${recommended ? `<span class="ribbon">Mi recomendación</span>` : ""}
    <h3>${esc(c.model)}</h3>
    <div class="fit">Encaja con tu perfil <div class="fit-bar"><span style="width:${c.score}%"></span></div> ${c.score}%</div>
    <div class="price">${money.format(best.price)}</div>
    <div class="price-sub">en ${esc(best.store_name)}${sub.length ? " · " + sub.join(" · ") : ""}</div>
    ${c.over_budget ? `<div class="price-sub over">Se pasa un poco de tu tope</div>` : ""}
    ${c.reason ? `<p class="reason">${esc(c.reason)}</p>` : ""}
    <ul class="checks">${(c.meets || []).map((m) => `<li>${esc(m)}</li>`).join("")}${(c.doubts || []).map((d) => `<li class="doubt">${esc(d)}</li>`).join("")}</ul>
    <div class="actions">
      <a class="btn btn-primary" href="${esc(best.url)}" target="_blank" rel="noopener">${best.store_name.length <= 12 ? `Ver en ${esc(best.store_name)}` : "Ver oferta"}</a>
      ${status.follow_enabled
        ? `<button class="btn follow-btn" data-follow="${cardModels.push(c) - 1}" title="Te avisamos por Telegram cuando baje de verdad">🔔 Seguir precio</button>`
        : `<button class="btn" disabled title="Próximamente: te avisamos cuando baje de verdad">Seguir precio</button>`}
    </div>
    <div class="follow-note" hidden></div>
    ${others.length ? `<details class="others"><summary>También en ${others.length} tienda${others.length > 1 ? "s" : ""} más</summary>
      <ul>${others.map((o) => `<li><a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.store_name)}</a><span>${money.format(o.price)}</span></li>`).join("")}</ul></details>` : ""}
  </article>`;
}

/* ---------------- Seguir precio ---------------- */
let cardModels = [];

function bindFollow() {
  for (const b of document.querySelectorAll(".follow-btn")) {
    b.addEventListener("click", async () => {
      const c = cardModels[Number(b.dataset.follow)];
      const note = b.closest(".card").querySelector(".follow-note");
      b.disabled = true; b.textContent = "Preparando…";
      try {
        const out = await api("/api/follow", {
          model: c.model, category: state.profile.category, product: state.profile.product,
          best: { price: c.best.price, store_name: c.best.store_name, url: c.best.url },
        });
        const a = document.createElement("a");
        a.className = "btn btn-primary"; a.href = out.link; a.target = "_blank"; a.rel = "noopener";
        a.textContent = "Abrir Telegram";
        b.replaceWith(a);
        note.hidden = false;
        note.innerHTML = `En Telegram tocá <b>Iniciar</b> y listo. Te escribimos cuando ${esc(c.model)} esté al menos 15 % más barato que su precio habitual (no contra el "precio de lista" inflado).`;
      } catch (e) {
        toast(e.message);
        b.disabled = false; b.textContent = "🔔 Seguir precio";
      }
    });
  }
}

function renderResults() {
  const r = state.results;
  if (!r) return;
  cardModels = [];
  const ok = r.stores.filter((s) => s.ok).length;
  const ml = `<section class="panel"><h2>Mirá también en Mercado Libre</h2>
      <p>Mercado Libre no permite que programas lean sus precios, así que no lo incluimos en la comparación. Te dejamos la búsqueda armada para que lo mires vos.</p>
      <div class="link-list">${r.links.mercadolibre.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.query)} en Mercado Libre</a>`).join("")}</div></section>`;
  const storeStatus = `<section class="panel"><details><summary><b>Dónde buscamos</b> · ${ok} de ${r.stores.length} tiendas respondieron · ${r.counts.found} publicaciones, ${r.counts.evaluated} evaluadas</summary>
      <ul class="store-status">${r.stores.map((s) => `<li class="${s.ok ? "" : "bad"}">${s.ok ? "✓" : "✗"} ${esc(s.name)} <small>${s.ok ? `${s.found} resultado${s.found === 1 ? "" : "s"}` : esc(s.error || "sin respuesta")}</small></li>`).join("")}</ul>
      <div class="link-list">${r.links.stores.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">Buscar en ${esc(l.name)}</a>`).join("")}</div></details></section>`;
  const near = (r.near || []).length ? `<section class="near"><h2>Casi cumplen</h2>
      <p class="blurb">Les falta algo de tu perfil (lo marcamos con ?), pero pueden servirte si aflojás ese punto.</p>
      <div class="tier-cards">${r.near.map((c) => card(c, false)).join("")}</div></section>` : "";
  const fb = feedbackBox();
  if (!r.tiers.length) {
    $("#results").innerHTML = `<div class="empty-state"><h2>No encontramos opciones que cumplan todo</h2>
      <p>Revisamos ${r.counts.found} publicaciones en ${ok} tiendas y ninguna encaja del todo con tu perfil. Probá corregir el perfil (por ejemplo, aflojar algún requisito) o mirá en Mercado Libre.</p>
      <p><button class="btn" id="back-profile">Volver al perfil</button></p></div>${near}${ml}${fb}${storeStatus}`;
    $("#back-profile").addEventListener("click", () => show("profile"));
    bindFeedback();
    bindFollow();
    return;
  }
  $("#results").innerHTML = `<div class="results-head"><h1>${esc(state.profile.product)}: tus opciones</h1>
      <p>${r.counts.matching} modelo${r.counts.matching === 1 ? "" : "s"} encaja${r.counts.matching === 1 ? "" : "n"} con tu perfil. Precios de hoy, de contado o transferencia cuando la tienda lo informa.</p>
      <p><button class="link-btn" id="back-profile">Ver mi perfil</button></p></div>
    <div class="tiers">${r.tiers.map((t) => `<section class="tier tier-${esc(t.key)}">
        <header class="tier-head">
          <h2>${esc(t.label)}</h2>
          <span class="range">${money.format(t.price_from)}${t.price_to > t.price_from ? " – " + money.format(t.price_to) : ""}</span>
          <p class="blurb">${esc(t.blurb)}</p>
        </header>
        <div class="tier-cards">${t.candidates.map((c) => card(c, c.model === r.recommended)).join("")}</div></section>`).join("")}</div>
    ${near}${ml}${fb}${storeStatus}`;
  $("#back-profile").addEventListener("click", () => show("profile"));
  bindFeedback();
  bindFollow();
}

/* ---------------- ¿Te sirvió? ---------------- */
function feedbackBox() {
  if (state.feedbackSent) return `<section class="panel feedback"><h2>¡Gracias por contarnos!</h2><p>Con lo que nos dicen vamos mejorando las preguntas y los resultados.</p></section>`;
  return `<section class="panel feedback" id="feedback">
      <h2>¿Te sirvió?</h2>
      <p>Estamos probando Compra Óptima con amigos y familia: contanos qué estuvo bien y qué no.</p>
      <div class="fb-choices">
        <button type="button" class="option" data-useful="1" aria-pressed="false">👍 Sí</button>
        <button type="button" class="option" data-useful="0" aria-pressed="false">👎 No mucho</button>
      </div>
      <label for="fb-comment" class="sr-only">Comentario</label>
      <textarea id="fb-comment" rows="3" maxlength="1500" placeholder="Opcional: ¿qué preguntas te parecieron raras? ¿Faltó algún producto? ¿Algún resultado no tenía sentido?"></textarea>
      <button type="button" class="btn btn-primary" id="fb-send" disabled>Enviar</button>
    </section>`;
}

function bindFeedback() {
  const box = $("#feedback");
  if (!box) return;
  let useful = null;
  for (const b of box.querySelectorAll("[data-useful]")) {
    b.addEventListener("click", () => {
      useful = b.dataset.useful === "1";
      for (const o of box.querySelectorAll("[data-useful]")) o.setAttribute("aria-pressed", String(o === b));
      $("#fb-send").disabled = false;
    });
  }
  $("#fb-send").addEventListener("click", async () => {
    const btn = $("#fb-send");
    btn.disabled = true;
    try {
      const r = state.results || {};
      await api("/api/feedback", {
        useful, comment: $("#fb-comment").value.trim(),
        product: state.profile?.product || "", recommended: r.recommended || "",
        matching: r.counts?.matching ?? null,
        queries: state.profile?.search_queries || [],
      });
      state.feedbackSent = true;
      save();
      box.outerHTML = feedbackBox();
    } catch (e) {
      toast(e.message);
      btn.disabled = false;
    }
  });
}

/* ---------------- Eventos ---------------- */
$("#code-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const code = $("#code-input").value.trim();
  const err = $("#code-error");
  err.hidden = true;
  if (!code) return;
  try {
    await api("/api/check-code", {}, code);
    setCode(code);
    $("#code-input").value = "";
    restoreView(viewAfterCode);
  } catch (ex) {
    err.textContent = ex.message; err.hidden = false;
  }
});
$("#start-form").addEventListener("submit", (e) => { e.preventDefault(); start($("#start-input").value); });
$("#start-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); start($("#start-input").value); }
});
for (const chip of document.querySelectorAll("#examples .chip")) {
  chip.addEventListener("click", () => { $("#start-input").value = chip.textContent; start(chip.textContent); });
}
$("#answer-form").addEventListener("submit", (e) => { e.preventDefault(); answer($("#answer-input").value); });
$("#explain-btn").addEventListener("click", () => answer("No sé, explicame las opciones"));
$("#finish-btn").addEventListener("click", () => ask(true));
$("#search-btn").addEventListener("click", search);
$("#adjust-btn").addEventListener("click", () => { state.adjusting = true; show("chat"); renderThread(); $("#answer-input").focus(); });
$("#restart-btn").addEventListener("click", () => {
  Object.assign(state, { conversation: [], last: null, profile: null, results: null, adjusting: false });
  $("#start-input").value = "";
  show("home");
});
$("#brand-link").addEventListener("click", (e) => { e.preventDefault(); show("home"); });

/* ---------------- Arranque ---------------- */
(async function init() {
  load();
  try {
    status = await api("/api/status");
    $("#store-count").textContent = status.stores;
    const badge = $("#provider-badge");
    badge.hidden = false;
    if (status.provider === "demo") { badge.textContent = "Modo demo (sin IA)"; badge.classList.add("warn"); }
    else badge.textContent = `IA: ${status.provider}`;
  } catch (_) { /* el servidor responde igual más tarde */ }
  if (status.code_required && !getCode()) { viewAfterCode = state.view; showCode(); return; }
  restoreView(state.view);
})();

function restoreView(view) {
  if (view === "chat" && state.conversation.length) { show("chat"); renderThread(); }
  else if (view === "profile" && state.profile) { renderProfile(); show("profile"); }
  else if (view === "results" && state.results) { renderProfile(); renderResults(); show("results"); }
  else show("home");
}
