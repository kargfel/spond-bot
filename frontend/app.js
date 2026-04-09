/**
 * SpondBot Frontend — Shared API & Utility Layer
 *
 * Responsibilities:
 *  - api()       : authenticated fetch wrapper (cookie sent automatically by browser)
 *  - showToast() : non-intrusive status notifications
 *  - fmtDate()   : consistent date formatting
 *  - badge helpers for status / choice rendering
 *
 * Authentication is handled via an HttpOnly session cookie set by the server.
 * The JWT is never accessible to JavaScript — cookies are sent automatically
 * with every same-origin request via `credentials: 'include'`.
 */

/* ── Config ────────────────────────────────────────────────────────── */
const BASE_URL = '/api/v1';

/* ── Core API wrapper ──────────────────────────────────────────────── */
/**
 * @param {string} path   - Path relative to /api/v1, e.g. "/events"
 * @param {string} method - HTTP method
 * @param {object} body   - JSON body (optional)
 * @returns {Promise<Response>}
 */
function api(path, method = 'GET', body = null) {
  const headers = { 'Content-Type': 'application/json' };
  const opts = { method, headers, credentials: 'include' };
  if (body !== null) opts.body = JSON.stringify(body);
  return fetch(BASE_URL + path, opts);
}

/* ── Auth guards ───────────────────────────────────────────────────── */
async function redirectToLogin() {
  window.location.href = 'index.html';
}

async function signOut() {
  // Ask the server to clear the HttpOnly cookie, then redirect
  await api('/auth/logout', 'POST').catch(() => {});
  window.location.href = 'index.html';
}

/**
 * Fetch the current user's claims from the server.
 * Returns the user object or null if unauthenticated.
 */
async function fetchCurrentUser() {
  try {
    const res = await api('/auth/me');
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/** Redirect to login if no valid session cookie is present. Returns user claims. */
async function requireAuth() {
  const user = await fetchCurrentUser();
  if (!user) {
    redirectToLogin();
    throw new Error('Redirecting to login');
  }
  return user;
}

/** Redirect to login if not admin. Returns user claims. */
async function requireAdmin() {
  const user = await requireAuth();
  if (!user.is_admin) window.location.href = 'dashboard.html';
  return user;
}

/* ── Toast notifications ───────────────────────────────────────────── */
function showToast(message, type = 'info', duration = 3500) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), duration);
}

/* ── Date formatting ───────────────────────────────────────────────── */
function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-GB', {
    weekday: 'short', day: '2-digit', month: 'short',
    year: 'numeric', hour: '2-digit', minute: '2-digit'
  });
}

function fmtDateShort(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric'
  });
}

function isOverdue(iso) {
  if (!iso) return false;
  return new Date(iso) < new Date();
}

/* ── Badge HTML helpers ────────────────────────────────────────────── */
function choiceBadge(choice) {
  const map = { accept: 'accept', decline: 'decline', manual: 'manual' };
  const label = { accept: 'Accept', decline: 'Decline', manual: 'Manual' };
  const cls = map[choice] || 'manual';
  return `<span class="badge badge-${cls}">${label[choice] || choice}</span>`;
}

function statusBadge(status) {
  const map = {
    processed: 'processed', pending: 'pending',
    failed: 'failed', processing: 'processing'
  };
  const cls = map[status] || 'manual';
  return `<span class="badge badge-${cls}">${status}</span>`;
}

/* ── SVG icon snippets ─────────────────────────────────────────────── */
const ICON = {
  calendar: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>`,
  clock:    `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
  sync:     `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.62"/></svg>`,
  user:     `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"  stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,
  logout:   `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>`,
  events:   `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>`,
  settings: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
  shield:   `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
  overview: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>`,
  plus:     `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`,
  trash:    `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"  stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>`,
};

/* ── Initials avatar helper ────────────────────────────────────────── */
function initials(name) {
  return (name || '?').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
}
