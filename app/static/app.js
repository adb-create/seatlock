// SeatLock web app: a small single-page app on top of the SeatLock REST API.
// No framework and no build step: the browser loads this file directly.

const MAX_SEATS = 10;          // matches MAX_SEATS_PER_BOOKING on the server
const SEATMAP_REFRESH_MS = 4000;

// ---------- small helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const rupees = (value) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(Number(value));

const dateParts = (iso) => {
  const d = new Date(iso);
  return {
    day: d.toLocaleDateString("en-IN", { day: "numeric" }),
    month: d.toLocaleDateString("en-IN", { month: "short" }),
    weekday: d.toLocaleDateString("en-IN", { weekday: "long" }),
    time: d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" }),
    full: d.toLocaleString("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" }),
  };
};

const newKey = () =>
  (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);

const store = {
  get(key) { try { return JSON.parse(localStorage.getItem(`seatlock.${key}`)); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(`seatlock.${key}`, JSON.stringify(value)); } catch { /* private mode */ } },
  del(key) { try { localStorage.removeItem(`seatlock.${key}`); } catch { /* ignore */ } },
};

function toast(message, kind = "info", ms = 4500) {
  const el = document.createElement("div");
  el.className = `toast toast--${kind}`;
  el.textContent = message;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), ms);
}

// ---------- API client ----------
class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

function errorText(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors
    return detail.map((d) => d.msg?.replace(/^Value error, /, "")).filter(Boolean).join(". ");
  }
  return "Something went wrong. Try again.";
}

async function api(method, path, { body, key, auth = true } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (key) headers["Idempotency-Key"] = key;
  if (auth && session.token) headers.Authorization = `Bearer ${session.token}`;

  let response;
  try {
    response = await fetch(path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError(0, "Can't reach SeatLock. Check that the server is running.");
  }
  let data = null;
  try { data = await response.json(); } catch { /* empty body */ }

  if (response.status === 401 && auth && session.token) {
    signOut(false);
    toast("Your session ended. Sign in again to continue.", "error");
  }
  if (!response.ok) throw new ApiError(response.status, errorText(data?.detail));
  return { status: response.status, data };
}

// ---------- session ----------
const session = { token: store.get("token"), user: store.get("user") };

async function loadMe() {
  if (!session.token) return;
  try {
    const { data } = await api("GET", "/auth/me");
    session.user = data;
    store.set("user", data);
  } catch { /* 401 handled in api() */ }
}

function signOut(rerender = true) {
  session.token = null;
  session.user = null;
  store.del("token");
  store.del("user");
  renderAccount();
  if (rerender) route();
}

function renderAccount() {
  const box = $("#account");
  $('[data-nav="admin"]').hidden = !session.user?.is_admin;
  if (session.user) {
    box.innerHTML = `<span class="account__email" title="${esc(session.user.email)}">${esc(session.user.email)}</span>
      <button class="btn btn--ghost btn--small" id="signout">Sign out</button>`;
    $("#signout").addEventListener("click", () => { signOut(); toast("Signed out."); });
  } else {
    box.innerHTML = `<button class="btn btn--ink btn--small" id="signin">Sign in</button>`;
    // Re-render the current page after signing in from the top bar
    // (e.g. My tickets switches from "sign in" to the ticket list).
    $("#signin").addEventListener("click", async () => { if (await openAuth()) route(); });
  }
}

// Sign-in dialog. Resolves true once the person is signed in.
let authResolve = null;
let authMode = "login";

function setAuthMode(mode) {
  authMode = mode;
  document.querySelectorAll("#auth-dialog .tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.mode === mode)));
  $("#auth-title").textContent = mode === "login" ? "Sign in to book" : "Create your account";
  $("#auth-submit").textContent = mode === "login" ? "Sign in" : "Create account";
  $("#auth-form [name=password]").autocomplete = mode === "login" ? "current-password" : "new-password";
  document.querySelectorAll("[data-register-only]").forEach((el) => { el.hidden = mode !== "register"; });
  $("#auth-error").hidden = true;
}

function openAuth(mode = "login") {
  setAuthMode(mode);
  $("#auth-form").reset();
  $("#auth-dialog").showModal();
  $("#auth-form [name=email]").focus();
  return new Promise((resolve) => { authResolve = resolve; });
}

function initAuthDialog() {
  const dialog = $("#auth-dialog");
  dialog.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setAuthMode(t.dataset.mode)));
  dialog.addEventListener("close", () => { authResolve?.(Boolean(session.token)); authResolve = null; });

  $("#auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const email = form.email.value.trim();
    const password = form.password.value;
    const error = $("#auth-error");
    const button = $("#auth-submit");

    if (!email.includes("@")) { error.textContent = "Enter a valid email address."; error.hidden = false; return; }
    if (password.length < 8) { error.textContent = "Passwords are at least 8 characters."; error.hidden = false; return; }

    button.disabled = true;
    error.hidden = true;
    try {
      if (authMode === "register") {
        await api("POST", "/auth/register", { body: { email, password }, auth: false });
      }
      const { data } = await api("POST", "/auth/login", { body: { email, password }, auth: false });
      session.token = data.access_token;
      store.set("token", session.token);
      await loadMe();
      renderAccount();
      dialog.close();
      toast(authMode === "register" ? "Account created. You're signed in." : "Signed in.", "success");
    } catch (err) {
      error.textContent = err.status === 409 ? "That email already has an account. Sign in instead." : err.message;
      error.hidden = false;
    } finally {
      button.disabled = false;
    }
  });
}

async function requireSignIn() {
  if (session.token) return true;
  return openAuth();
}

// Confirmation dialog that returns a promise instead of blocking like window.confirm.
function confirmAction({ title, body, yes }) {
  const dialog = $("#confirm-dialog");
  $("#confirm-title").textContent = title;
  $("#confirm-body").textContent = body;
  $("#confirm-yes").textContent = yes;
  dialog.showModal();
  return new Promise((resolve) => {
    const onClick = (event) => {
      const answer = event.target.closest("[data-answer]")?.dataset.answer;
      if (!answer) return;
      dialog.removeEventListener("click", onClick);
      dialog.close();
      resolve(answer === "yes");
    };
    dialog.addEventListener("click", onClick);
    dialog.addEventListener("cancel", () => { dialog.removeEventListener("click", onClick); resolve(false); }, { once: true });
  });
}

// ---------- router ----------
let cleanup = () => {};

const routes = [
  [/^#?\/?$/, renderHome, "shows"],
  [/^#\/show\/(\d+)$/, renderShow, "shows"],
  [/^#\/tickets$/, renderTickets, "tickets"],
  [/^#\/admin$/, renderAdmin, "admin"],
];

async function route() {
  cleanup();
  cleanup = () => {};
  const hash = location.hash || "#/";
  const match = routes.find(([re]) => re.test(hash));
  const [re, render, nav] = match || routes[0];
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === nav) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  view.innerHTML = `<p class="loading">Loading…</p>`;
  try {
    await render(...(hash.match(re) || []).slice(1));
  } catch (err) {
    view.innerHTML = `<div class="empty"><h2>This page didn't load</h2><p class="muted">${esc(err.message)}</p>
      <a class="btn btn--ghost" href="#/">Back to shows</a></div>`;
  }
}

// ---------- home: list of shows ----------
function fillBar(event) {
  const sold = event.total_seats - event.available_seats;
  const pct = event.total_seats ? Math.round((sold / event.total_seats) * 100) : 0;
  const label = event.available_seats === 0 ? "Sold out" : `${event.available_seats} of ${event.total_seats} seats left`;
  return `<div class="fill"><div class="fill__bar" aria-hidden="true"><span style="width:${pct}%"></span></div>
    <span class="fill__label">${label}</span></div>`;
}

function priceText(event) {
  if (event.min_price == null) return "";
  return Number(event.min_price) === Number(event.max_price)
    ? rupees(event.min_price)
    : `${rupees(event.min_price)} to ${rupees(event.max_price)}`;
}

async function renderHome() {
  const { data: events } = await api("GET", "/events", { auth: false });
  const upcoming = events.filter((e) => new Date(e.starts_at) > Date.now() - 3 * 3600e3);
  document.title = "SeatLock — book seats for live shows";

  if (!upcoming.length) {
    view.innerHTML = `<div class="empty"><h2>No shows on sale yet</h2>
      <p class="muted">${session.user?.is_admin ? "Add the first show and it appears here." : "Check back soon."}</p>
      ${session.user?.is_admin ? `<a class="btn btn--primary" href="#/admin">Add a show</a>` : ""}</div>`;
    return;
  }

  const [next, ...rest] = upcoming;
  const d = dateParts(next.starts_at);
  view.innerHTML = `
    <section class="hero" aria-labelledby="hero-title">
      <article class="stub hero__stub">
        <div class="stub__date"><span class="stub__day">${d.day}</span><span class="stub__month">${d.month}</span></div>
        <div class="hero__body">
          <h1 class="hero__title" id="hero-title">${esc(next.name)}</h1>
          <div class="hero__facts">
            <span><strong>${esc(next.venue)}</strong></span>
            <span>${d.weekday}, ${d.time}</span>
            <span>${priceText(next)}</span>
          </div>
          <div class="hero__cta">
            <a class="btn btn--primary" href="#/show/${next.id}">${next.available_seats ? "Choose your seats" : "View seat map"}</a>
            ${fillBar(next)}
          </div>
        </div>
      </article>
    </section>
    ${rest.length ? `<section aria-labelledby="more-title">
      <h2 class="section-title" id="more-title">More shows</h2>
      <ul class="show-list">${rest.map(showStub).join("")}</ul>
    </section>` : ""}`;
}

function showStub(event) {
  const d = dateParts(event.starts_at);
  return `<li><a class="stub" href="#/show/${event.id}">
    <div class="stub__date"><span class="stub__day">${d.day}</span><span class="stub__month">${d.month}</span></div>
    <div class="stub__body"><h3 class="stub__title">${esc(event.name)}</h3>
      <p class="stub__meta">${esc(event.venue)}, ${d.weekday} ${d.time}</p></div>
    <div class="stub__aside"><strong>${priceText(event)}</strong>${fillBar(event)}</div>
  </a></li>`;
}

// ---------- show page: seat map + checkout ----------
async function renderShow(eventId) {
  eventId = Number(eventId);
  const [{ data: event }, { data: seats }] = await Promise.all([
    api("GET", `/events/${eventId}`, { auth: false }),
    api("GET", `/events/${eventId}/seats`, { auth: false }),
  ]);
  document.title = `${event.name} — SeatLock`;
  const d = dateParts(event.starts_at);

  const state = {
    seats,                       // latest seat map from the server
    selected: new Set(),         // seat ids picked but not held yet
    booking: null,               // the signed-in user's PENDING booking for this show
    holdKey: null,               // Idempotency-Key for the current selection
    payKey: null,                // Idempotency-Key for the current payment attempt
    busy: false,
    timer: null,
  };

  view.innerHTML = `
    <a class="back" href="#/">&larr; All shows</a>
    <div class="show-head">
      <div>
        <h1>${esc(event.name)}</h1>
        <p class="muted">${esc(event.venue)}, ${d.full}</p>
      </div>
      <span class="live"><span class="live__dot" aria-hidden="true"></span>Seat map updates live</span>
    </div>
    <div class="booking-layout">
      <section class="hall" aria-label="Seat map">
        <div class="stage" aria-hidden="true">Stage</div>
        <div class="seatmap"><div class="seatmap__grid" id="grid" role="group" aria-label="Seats, front row first"></div></div>
        <div class="legend" aria-hidden="true">
          <span><i class="seat"></i>Available</span>
          <span><i class="seat seat--selected"></i>Your seats</span>
          <span><i class="seat seat--held"></i>Someone is paying</span>
          <span><i class="seat seat--booked"></i>Sold</span>
        </div>
        <div class="prices" id="prices"></div>
      </section>
      <aside class="panel" id="panel" aria-live="polite"></aside>
    </div>`;

  const grid = $("#grid");
  const panel = $("#panel");

  // Price tiers, e.g. rows A–B premium, the rest standard.
  const tiers = [];
  const rowPrice = new Map();
  for (const seat of seats) if (!rowPrice.has(seat.row)) rowPrice.set(seat.row, seat.price);
  for (const [row, price] of rowPrice) {
    const last = tiers[tiers.length - 1];
    if (last && Number(last.price) === Number(price)) last.to = row;
    else tiers.push({ price, from: row, to: row });
  }
  const premiumPrice = tiers.length > 1 ? Math.max(...tiers.map((t) => Number(t.price))) : null;
  $("#prices").innerHTML = tiers
    .map((t) => `<span>Row${t.from === t.to ? ` ${t.from}` : `s ${t.from} to ${t.to}`}: <strong>${rupees(t.price)}</strong></span>`)
    .join("");

  const myHeldIds = () => new Set(state.booking ? state.booking.seats.map((s) => s.id) : []);

  function seatClass(seat) {
    const classes = ["seat"];
    if (premiumPrice && Number(seat.price) === premiumPrice) classes.push("seat--premium");
    if (myHeldIds().has(seat.id)) classes.push("seat--mine");
    else if (state.selected.has(seat.id)) classes.push("seat--selected");
    else if (seat.status === "HELD") classes.push("seat--held");
    else if (seat.status === "BOOKED") classes.push("seat--booked");
    return classes.join(" ");
  }

  function seatLabel(seat) {
    const mine = myHeldIds().has(seat.id);
    const status = mine ? "held for you" : state.selected.has(seat.id) ? "selected"
      : seat.status === "HELD" ? "being booked by someone else" : seat.status === "BOOKED" ? "sold" : "available";
    return `Seat ${seat.label}, ${rupees(seat.price)}, ${status}`;
  }

  function drawGrid() {
    const rows = new Map();
    for (const seat of state.seats) {
      if (!rows.has(seat.row)) rows.set(seat.row, []);
      rows.get(seat.row).push(seat);
    }
    let lastPrice = null;
    let html = "";
    for (const [row, rowSeats] of rows) {
      rowSeats.sort((a, b) => a.number - b.number);
      if (lastPrice !== null && rowSeats[0].price !== lastPrice) html += `<div class="tier-break" aria-hidden="true"></div>`;
      lastPrice = rowSeats[0].price;
      const aisleAfter = rowSeats.length >= 8 ? Math.ceil(rowSeats.length / 2) : 0;
      html += `<div class="seat-row"><span class="seat-row__label" aria-hidden="true">${esc(row)}</span>`;
      rowSeats.forEach((seat, i) => {
        const takenByOthers = !myHeldIds().has(seat.id) && (seat.status !== "AVAILABLE");
        const locked = Boolean(state.booking);   // can't change seats while holding
        html += `<button type="button" class="${seatClass(seat)}" data-id="${seat.id}"
          aria-label="${esc(seatLabel(seat))}" aria-pressed="${state.selected.has(seat.id)}"
          ${takenByOthers || locked ? "disabled" : ""}>${seat.number}</button>`;
        if (aisleAfter && i + 1 === aisleAfter) html += `<span class="seat-row__aisle" aria-hidden="true"></span>`;
      });
      html += `<span class="seat-row__label" aria-hidden="true">${esc(row)}</span></div>`;
    }
    grid.innerHTML = html;
    fitSeats(rows);
  }

  // Update existing seat buttons in place (keeps keyboard focus and hover).
  function updateGrid() {
    const locked = Boolean(state.booking);
    for (const seat of state.seats) {
      const button = grid.querySelector(`[data-id="${seat.id}"]`);
      if (!button) { drawGrid(); return; }
      const takenByOthers = !myHeldIds().has(seat.id) && seat.status !== "AVAILABLE";
      const cls = seatClass(seat);
      if (button.className.replace(" seat--changed", "") !== cls) button.className = cls;
      button.disabled = takenByOthers || locked;
      button.setAttribute("aria-pressed", String(state.selected.has(seat.id)));
      button.setAttribute("aria-label", seatLabel(seat));
    }
  }

  // Shrink seats so the widest row fits the hall without sideways scrolling
  // (down to 14px; below that the map scrolls instead).
  function fitSeats(rows) {
    const widest = Math.max(...[...rows.values()].map((r) => r.length));
    const hasAisle = widest >= 8;
    const available = grid.parentElement.clientWidth - 8;
    const gap = parseFloat(getComputedStyle(grid).getPropertyValue("--gap")) || 6;
    const labels = 2 * (1.4 * 16 + gap);
    const slots = widest + (hasAisle ? 0.8 : 0);
    const size = Math.floor((available - labels - gap * (widest + (hasAisle ? 1 : 0))) / slots);
    grid.style.setProperty("--seat", `${Math.max(14, Math.min(26, size))}px`);
  }
  const onResize = () => drawGrid();
  window.addEventListener("resize", onResize);

  grid.addEventListener("click", (event) => {
    const button = event.target.closest(".seat[data-id]");
    if (!button || button.disabled || state.booking) return;
    const id = Number(button.dataset.id);
    if (state.selected.has(id)) {
      state.selected.delete(id);
    } else {
      if (state.selected.size >= MAX_SEATS) { toast(`You can book up to ${MAX_SEATS} seats at once.`, "error"); return; }
      state.selected.add(id);
    }
    state.holdKey = null;   // new selection = new request
    button.className = seatClass(state.seats.find((s) => s.id === id));
    button.setAttribute("aria-pressed", String(state.selected.has(id)));
    button.setAttribute("aria-label", seatLabel(state.seats.find((s) => s.id === id)));
    drawPanel();
  });

  // --- panel: selection or checkout ---
  const panelKey = () => (state.booking ? `b${state.booking.id}` : `s${[...state.selected].sort().join(",")}`);

  function drawPanel() {
    clearInterval(state.timer);
    state.lastPanelKey = panelKey();
    if (state.booking) return drawCheckout();

    const picked = state.seats.filter((s) => state.selected.has(s.id));
    const total = picked.reduce((sum, s) => sum + Number(s.price), 0);
    panel.innerHTML = `
      <div class="panel__head"><h2>Your seats</h2></div>
      <div class="panel__body">
        ${picked.length
          ? `<div class="chips">${picked.map((s) => `<span class="chip">${esc(s.label)}</span>`).join("")}</div>`
          : `<p class="muted">Tap the seats you want on the map. You can pick up to ${MAX_SEATS}.</p>`}
        <div class="total"><span>Total</span><strong>${rupees(total)}</strong></div>
        <button class="btn btn--primary btn--block" id="hold" ${picked.length ? "" : "disabled"}>
          ${picked.length ? `Hold ${picked.length} seat${picked.length > 1 ? "s" : ""}` : "Pick seats to continue"}
        </button>
        <p class="note">We keep your seats for 5 minutes while you pay. Nobody else can take them in that time.</p>
      </div>`;
    $("#hold")?.addEventListener("click", holdSeats);
  }

  function drawCheckout() {
    const b = state.booking;
    panel.innerHTML = `
      <div class="panel__head"><h2>Seats held for you</h2></div>
      <div class="panel__body">
        <div class="chips">${b.seats.map((s) => `<span class="chip">${esc(s.label)}</span>`).join("")}</div>
        <div class="timer" id="timer">
          <span class="muted">Time left to pay</span>
          <span class="timer__text" id="timer-text">5:00</span>
          <div class="timer__bar" aria-hidden="true"><span id="timer-bar" style="width:100%"></span></div>
        </div>
        <div class="total"><span>Total</span><strong>${rupees(b.total_amount)}</strong></div>
        <button class="btn btn--primary btn--block" id="pay">Pay ${rupees(b.total_amount)}</button>
        <label class="check"><input type="checkbox" id="decline"> Simulate a declined card (to see what happens)</label>
        <button class="btn btn--ghost btn--block" id="release">Release these seats</button>
        <p class="note">This is a demo checkout. No card details are needed and no money is charged.</p>
      </div>`;
    $("#pay").addEventListener("click", pay);
    $("#release").addEventListener("click", release);
    tick();
    state.timer = setInterval(tick, 1000);
  }

  function tick() {
    if (!state.booking) return;
    const total = 300; // seconds; matches HOLD_TTL_SECONDS default
    const left = Math.max(0, Math.round((new Date(state.booking.expires_at) - Date.now()) / 1000));
    const text = $("#timer-text");
    if (!text) return;
    text.textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
    $("#timer-bar").style.width = `${Math.min(100, (left / total) * 100)}%`;
    $("#timer").classList.toggle("timer--low", left <= 60);
    if (left === 0) {
      clearInterval(state.timer);
      state.booking = null;
      state.selected.clear();
      toast("Your hold ran out, so the seats were released. Pick seats again to book.", "error", 7000);
      refresh();
    }
  }

  async function holdSeats() {
    if (state.busy || !state.selected.size) return;
    if (!(await requireSignIn())) return;
    state.busy = true;
    const button = $("#hold");
    button.disabled = true;
    button.textContent = "Holding seats…";
    state.holdKey ||= newKey();   // reused if the person retries the same selection
    try {
      const { data } = await api("POST", "/bookings", {
        body: { event_id: eventId, seat_ids: [...state.selected] },
        key: state.holdKey,
      });
      state.booking = data;
      state.selected.clear();
      state.payKey = newKey();
      toast(`Seats ${data.seats.map((s) => s.label).join(", ")} are held for 5 minutes.`, "success");
    } catch (err) {
      if (err.status === 409) {
        toast(`${err.message}. Someone got there first, so pick other seats.`, "error", 7000);
        state.selected.clear();
        state.holdKey = null;
      } else if (err.status === 429) {
        toast("Too many attempts. Wait a minute and try again.", "error");
      } else {
        toast(err.message, "error");
      }
    } finally {
      state.busy = false;
      await refresh();
      drawPanel();   // always restore the button, whatever happened
    }
  }

  async function pay() {
    if (state.busy || !state.booking) return;
    state.busy = true;
    const button = $("#pay");
    button.disabled = true;
    button.textContent = "Processing payment…";
    const token = $("#decline").checked ? "tok_fail" : "tok_success";
    try {
      const { data } = await api("POST", `/bookings/${state.booking.id}/confirm`, {
        body: { payment_token: token },
        key: state.payKey,
      });
      clearInterval(state.timer);
      state.booking = null;
      cleanup();
      showConfirmation(data);
      return;
    } catch (err) {
      if (err.status === 402) {
        toast("The card was declined. Your seats are still held, so try paying again.", "error", 7000);
      } else if (err.status === 410) {
        toast("Your hold ran out before payment. Pick seats again to book.", "error", 7000);
        state.booking = null;
      } else {
        toast(err.message, "error");
      }
      state.busy = false;
      await refresh();
      drawPanel();   // restore the Pay button after a failed attempt
    }
  }

  async function release() {
    if (!state.booking) return;
    const ok = await confirmAction({
      title: "Release these seats?",
      body: "Other people will be able to book them straight away.",
      yes: "Release seats",
    });
    if (!ok) return;
    try {
      await api("POST", `/bookings/${state.booking.id}/cancel`);
      state.booking = null;
      toast("Seats released.");
    } catch (err) {
      toast(err.message, "error");
    }
    await refresh();
  }

  function showConfirmation(booking) {
    const dd = dateParts(booking.event_starts_at);
    view.innerHTML = `
      <div class="empty" style="border-style:solid;border-color:var(--ink)">
        <h2>You're going to ${esc(booking.event_name)}</h2>
        <p>Seats <strong>${booking.seats.map((s) => esc(s.label)).join(", ")}</strong>, ${dd.full}</p>
        <p class="muted">Paid ${rupees(booking.total_amount)}. Booking #${booking.id}, payment reference ${esc(booking.payment_ref)}.</p>
        <div class="hero__cta" style="justify-content:center">
          <a class="btn btn--primary" href="#/tickets">See my tickets</a>
          <a class="btn btn--ghost" href="#/">Book another show</a>
        </div>
      </div>`;
    view.focus();
    toast("Payment confirmed. Enjoy the show!", "success");
  }

  // Find a PENDING, unexpired booking this user already has for this show
  // (e.g. they refreshed the page during checkout).
  async function loadMyHold() {
    if (!session.token) { state.booking = null; return; }
    try {
      const { data } = await api("GET", "/bookings");
      const now = Date.now();
      state.booking = data.find((b) => b.event_id === eventId && b.status === "PENDING" && new Date(b.expires_at) > now) || null;
      if (state.booking && !state.payKey) state.payKey = newKey();
    } catch { state.booking = null; }
  }

  // Re-fetch the seat map; flash seats that someone else just took.
  async function refresh() {
    try {
      const { data } = await api("GET", `/events/${eventId}/seats`, { auth: false });
      const before = new Map(state.seats.map((s) => [s.id, s.status]));
      state.seats = data;
      // drop selections that are no longer available
      for (const id of [...state.selected]) {
        if (data.find((s) => s.id === id)?.status !== "AVAILABLE") {
          state.selected.delete(id);
          state.holdKey = null;
        }
      }
      const hadBooking = Boolean(state.booking);
      if (!state.booking) await loadMyHold();
      updateGrid();
      // Only rebuild the panel when its content changes, so the countdown
      // and buttons aren't reset on every poll.
      if (panelKey() !== state.lastPanelKey || hadBooking !== Boolean(state.booking)) drawPanel();
      for (const seat of data) {
        if (before.get(seat.id) === "AVAILABLE" && seat.status !== "AVAILABLE" && !myHeldIds().has(seat.id)) {
          grid.querySelector(`[data-id="${seat.id}"]`)?.classList.add("seat--changed");
        }
      }
    } catch { /* keep the last good map; the next poll will try again */ }
  }

  await loadMyHold();
  drawGrid();
  drawPanel();

  // Live updates: poll while the page is visible.
  const poll = setInterval(() => { if (!document.hidden && !state.busy) refresh(); }, SEATMAP_REFRESH_MS);
  cleanup = () => { clearInterval(poll); clearInterval(state.timer); window.removeEventListener("resize", onResize); };
}

// ---------- my tickets ----------
async function renderTickets() {
  document.title = "My tickets — SeatLock";
  if (!session.token) {
    view.innerHTML = `<div class="empty"><h2>Sign in to see your tickets</h2>
      <p class="muted">Your bookings appear here after you sign in.</p>
      <button class="btn btn--primary" id="tickets-signin">Sign in</button></div>`;
    $("#tickets-signin").addEventListener("click", async () => { if (await openAuth()) route(); });
    return;
  }
  const { data: bookings } = await api("GET", "/bookings");
  const now = Date.now();
  // A PENDING booking past its expiry is already free; show it as expired.
  for (const b of bookings) if (b.status === "PENDING" && new Date(b.expires_at) <= now) b.status = "EXPIRED";

  if (!bookings.length) {
    view.innerHTML = `<h1 class="page-title">My tickets</h1>
      <div class="empty"><h2>No tickets yet</h2><p class="muted">Pick a show and choose your seats.</p>
      <a class="btn btn--primary" href="#/">Browse shows</a></div>`;
    return;
  }

  const labels = { CONFIRMED: "Confirmed", PENDING: "Waiting for payment", CANCELLED: "Cancelled", EXPIRED: "Hold expired" };
  view.innerHTML = `<h1 class="page-title">My tickets</h1>
    <p class="page-intro">Show the booking number at the venue. Cancelling a ticket frees the seats for other people.</p>
    <ul class="show-list">${bookings.map((b) => {
      const d = dateParts(b.event_starts_at);
      const seatsText = b.seats.map((s) => esc(s.label)).join(", ");
      return `<li><article class="stub ticket ticket--${b.status.toLowerCase()}">
        <div class="stub__date"><span class="stub__day">${d.day}</span><span class="stub__month">${d.month}</span></div>
        <div class="stub__body">
          <h2 class="stub__title">${esc(b.event_name)}</h2>
          <p class="stub__meta">${d.weekday} ${d.time}. Seat${b.seats.length > 1 ? "s" : ""} <strong>${seatsText}</strong></p>
          <p class="ref">Booking #${b.id}${b.payment_ref ? `, payment ${esc(b.payment_ref)}` : ""}</p>
        </div>
        <div class="stub__aside">
          <span class="badge badge--${b.status}">${labels[b.status]}</span>
          <strong>${rupees(b.total_amount)}</strong>
          ${b.status === "PENDING" ? `<a class="btn btn--primary btn--small" href="#/show/${b.event_id}">Finish paying</a>` : ""}
          ${["PENDING", "CONFIRMED"].includes(b.status) ? `<button class="btn btn--ghost btn--small" data-cancel="${b.id}">Cancel</button>` : ""}
        </div>
      </article></li>`;
    }).join("")}</ul>`;

  view.querySelectorAll("[data-cancel]").forEach((button) => button.addEventListener("click", async () => {
    const id = Number(button.dataset.cancel);
    const ok = await confirmAction({
      title: `Cancel booking #${id}?`,
      body: "Your seats go back on sale straight away. This can't be undone.",
      yes: "Cancel booking",
    });
    if (!ok) return;
    try {
      await api("POST", `/bookings/${id}/cancel`);
      toast(`Booking #${id} cancelled.`, "success");
      route();
    } catch (err) { toast(err.message, "error"); }
  }));
}

// ---------- admin: add a show ----------
async function renderAdmin() {
  document.title = "Add a show — SeatLock";
  if (!session.user?.is_admin) {
    view.innerHTML = `<div class="empty"><h2>Only organisers can add shows</h2>
      <p class="muted">Sign in with an organiser account to add a show.</p>
      ${session.token ? "" : `<button class="btn btn--primary" id="admin-signin">Sign in</button>`}</div>`;
    $("#admin-signin")?.addEventListener("click", async () => { if (await openAuth()) route(); });
    return;
  }
  const soon = new Date(Date.now() + 14 * 864e5);
  soon.setHours(19, 0, 0, 0);
  const local = new Date(soon.getTime() - soon.getTimezoneOffset() * 6e4).toISOString().slice(0, 16);

  view.innerHTML = `<h1 class="page-title">Add a show</h1>
    <p class="page-intro">Seats are created automatically: rows are lettered from A at the front.
    Give the front rows a premium price if you like.</p>
    <form class="form" id="event-form" style="max-width:640px">
      <label class="field"><span>Show name</span><input name="name" required maxlength="200" placeholder="Shreya Ghoshal Live"></label>
      <label class="field"><span>Venue</span><input name="venue" required maxlength="200" placeholder="Science City Auditorium, Kolkata"></label>
      <label class="field"><span>Date and time</span><input name="starts_at" type="datetime-local" required value="${local}"></label>
      <div class="form__row">
        <label class="field"><span>Rows</span><input name="rows" type="number" min="1" max="26" value="8" required></label>
        <label class="field"><span>Seats per row</span><input name="seats_per_row" type="number" min="1" max="50" value="16" required></label>
        <label class="field"><span>Ticket price (₹)</span><input name="price" type="number" min="1" step="1" value="699" required></label>
      </div>
      <div class="form__row">
        <label class="field"><span>Premium front rows</span><input name="premium_rows" type="number" min="0" max="26" value="2"></label>
        <label class="field"><span>Premium price (₹)</span><input name="premium_price" type="number" min="1" step="1" value="1299"></label>
      </div>
      <p class="form__error" id="event-error" role="alert" hidden></p>
      <div><button class="btn btn--primary" type="submit">Add show</button></div>
    </form>`;

  $("#event-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = event.currentTarget;
    const error = $("#event-error");
    const premiumRows = Number(f.premium_rows.value || 0);
    const body = {
      name: f.name.value.trim(),
      venue: f.venue.value.trim(),
      starts_at: new Date(f.starts_at.value).toISOString(),
      rows: Number(f.rows.value),
      seats_per_row: Number(f.seats_per_row.value),
      price: Number(f.price.value).toFixed(2),
      premium_rows: premiumRows,
      premium_price: premiumRows ? Number(f.premium_price.value).toFixed(2) : null,
    };
    error.hidden = true;
    try {
      const { data } = await api("POST", "/events", { body });
      toast(`${data.name} is on sale with ${data.total_seats} seats.`, "success");
      location.hash = `#/show/${data.id}`;
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  });
}

// ---------- start ----------
initAuthDialog();
renderAccount();
window.addEventListener("hashchange", () => { route(); view.focus({ preventScroll: true }); });
loadMe().then(() => { renderAccount(); route(); });
