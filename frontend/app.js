/**
 * SpondBot Frontend — shared browser layer for all pages.
 *
 *  - api() / apiJson()   authenticated fetch (HttpOnly session cookie, sent automatically)
 *  - requireAuth()       redirect to sign-in without a session; requireAdmin() for admin pages
 *  - toast()             short status messages (role=status, or role=alert for errors)
 *  - openDialog()        native <dialog> helpers; confirmAction() replaces window.confirm()
 *  - bindMenu()          accessible popup menu behind a button
 *
 * Pure formatting and event logic lives in core.js (global `Core`).
 */

const BASE_URL = "/api/v1";

/* ── API ───────────────────────────────────────────────────────────── */
function api(path, method = "GET", body = null) {
  const opts = { method, headers: { "Content-Type": "application/json" }, credentials: "include" };
  if (body !== null) opts.body = JSON.stringify(body);
  return fetch(BASE_URL + path, opts);
}

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/** Fetch JSON; throws ApiError carrying the server's `detail` message on failure. */
async function apiJson(path, method = "GET", body = null) {
  let res;
  try {
    res = await api(path, method, body);
  } catch {
    throw new ApiError("Could not reach SpondBot. Check your connection and try again.", 0);
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : `Request failed (${res.status}).`;
    throw new ApiError(detail, res.status);
  }
  return data;
}

/* ── Auth ──────────────────────────────────────────────────────────── */
async function fetchCurrentUser() {
  try {
    const res = await api("/auth/me");
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

async function requireAuth() {
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = "/";
    throw new Error("Redirecting to sign in");
  }
  return user;
}

async function requireAdmin() {
  const user = await requireAuth();
  if (!user.is_admin) {
    window.location.href = "/dashboard";
    throw new Error("Redirecting to dashboard");
  }
  return user;
}

async function signOut() {
  await api("/auth/logout", "POST").catch(() => {});
  window.location.href = "/";
}

/* ── Toasts ────────────────────────────────────────────────────────── */
function toast(message, type = "info", duration = 4000) {
  let box = document.getElementById("toasts");
  if (!box) {
    box = document.createElement("div");
    box.id = "toasts";
    box.className = "toasts";
    document.body.appendChild(box);
  }
  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  el.setAttribute("role", type === "error" ? "alert" : "status");
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

/* ── Dialogs ───────────────────────────────────────────────────────── */
function openDialog(id) {
  const dlg = document.getElementById(id);
  dlg.querySelectorAll("[data-error]").forEach((e) => { e.hidden = true; e.textContent = ""; });
  dlg.showModal();
  return dlg;
}

function closeDialog(id) {
  document.getElementById(id).close();
}

function showDialogError(id, message) {
  const el = document.getElementById(id).querySelector("[data-error]");
  el.textContent = message;
  el.hidden = false;
}

/** Close any dialog when its backdrop or a [data-close] button is clicked. */
function wireDialogs() {
  document.querySelectorAll("dialog").forEach((dlg) => {
    dlg.addEventListener("click", (e) => {
      if (e.target === dlg || e.target.closest("[data-close]")) dlg.close();
    });
  });
}

/**
 * In-page replacement for window.confirm(). Resolves true when the action is confirmed.
 * Requires a <dialog id="confirm-dialog"> with [data-confirm-title], [data-confirm-body]
 * and a [data-confirm-ok] button (see admin.html).
 */
function confirmAction(title, body, okLabel) {
  const dlg = document.getElementById("confirm-dialog");
  dlg.querySelector("[data-confirm-title]").textContent = title;
  dlg.querySelector("[data-confirm-body]").textContent = body;
  const ok = dlg.querySelector("[data-confirm-ok]");
  ok.textContent = okLabel;
  return new Promise((resolve) => {
    const onOk = () => { dlg.close("ok"); };
    ok.addEventListener("click", onOk, { once: true });
    dlg.addEventListener("close", () => {
      ok.removeEventListener("click", onOk);
      resolve(dlg.returnValue === "ok");
      dlg.returnValue = "";
    }, { once: true });
    dlg.showModal();
  });
}

/* ── Menu ──────────────────────────────────────────────────────────── */
function bindMenu(button, menu) {
  const items = () => [...menu.querySelectorAll('[role="menuitem"]:not([hidden])')];
  const close = () => {
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
  };
  const open = () => {
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    items()[0]?.focus();
  };
  button.addEventListener("click", (e) => {
    e.stopPropagation();
    menu.hidden ? open() : close();
  });
  menu.addEventListener("click", (e) => {
    if (e.target.closest('[role="menuitem"]')) close();
  });
  menu.addEventListener("keydown", (e) => {
    const list = items();
    const i = list.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); list[(i + 1) % list.length].focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); list[(i - 1 + list.length) % list.length].focus(); }
    if (e.key === "Escape") { close(); button.focus(); }
  });
  document.addEventListener("click", (e) => {
    if (!menu.hidden && !menu.contains(e.target)) close();
  });
}

/* ── Live stream ───────────────────────────────────────────────────── */
/** Connects to an SSE endpoint; toggles the [hidden] state of `indicator` with the connection. */
function connectStream(path, handlers, indicator) {
  if (!window.EventSource) return null;
  const es = new EventSource(BASE_URL + path, { withCredentials: true });
  es.onopen = () => { if (indicator) indicator.hidden = false; };
  es.onerror = () => { if (indicator) indicator.hidden = true; };
  for (const [name, fn] of Object.entries(handlers)) {
    es.addEventListener(name, (e) => {
      let data = {};
      try { data = JSON.parse(e.data); } catch {}
      fn(data);
    });
  }
  return es;
}

/* ── Misc ──────────────────────────────────────────────────────────── */
function initials(name) {
  return (name || "?").trim().split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
}

const esc = (s) => Core.escapeHtml(s);
