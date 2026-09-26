/**
 * Admin panel.
 *
 * Views (hash routed: #queue, #timeline, #users, #log, #charts):
 *  - Queue     next-fire countdown, health counters, every account's events
 *              ordered by fire time with answer toggles, send-now / disarm / retry
 *  - Timeline  one lane per Spond account: registration opening -> event start
 *  - Users     dashboard logins and Spond accounts
 *  - Log       RSVP audit log
 *  - Charts    latency and daily outcomes (Chart.js), per-account table
 *
 * Depends on core.js (Core) and app.js (api helpers, dialogs, toasts).
 */
(function () {
  const L = Core.CHOICE_LABELS;
  const SHORT = { accept: "ACC", decline: "DEC", manual: "MAN" };
  const CHOICES = ["accept", "decline", "manual"];
  const VIEWS = ["queue", "timeline", "users", "log", "charts"];
  const DAY = 24 * 3600e3;

  const state = {
    me: null,
    view: "queue",
    spondUsers: [],
    accounts: [],
    events: [],
    jobs: [],
    stats: null,
    selectedEvent: null,
    editingLogin: null,
    charts: {},
  };

  const $ = (id) => document.getElementById(id);
  const nameOf = (id) => state.spondUsers.find((u) => u.id === id)?.display_name || String(id || "").slice(0, 8);
  const eventById = (id) => state.events.find((e) => e.id === id);

  /* ── Loading ─────────────────────────────────────────────────────── */
  /** Runs an API call; on failure shows the error and returns `undefined`
   *  (a successful 204 response resolves to `null`, so the two never collide). */
  const FAILED = undefined;
  async function guarded(fn) {
    try {
      return await fn();
    } catch (err) {
      if (err.status === 401) window.location.href = "/";
      else toast(err.message, "error", 6000);
      return FAILED;
    }
  }

  async function loadSpondUsers() {
    state.spondUsers = (await guarded(() => apiJson("/spond-accounts"))) || [];
    fillAccountSelects();
  }
  async function loadEvents() {
    state.events = (await guarded(() => apiJson("/events?all=true"))) || state.events;
  }
  async function loadJobs() {
    state.jobs = (await guarded(() => apiJson("/admin/scheduler"))) || state.jobs;
  }
  async function loadStats() {
    state.stats = (await guarded(() => apiJson("/admin/stats"))) || state.stats;
    renderCounters();
  }

  async function refreshQueue() {
    await Promise.all([loadEvents(), loadJobs()]);
    renderQueue();
    if (state.view === "timeline") renderTimeline();
  }

  function fillAccountSelects() {
    for (const id of ["q-account", "log-account", "chart-account"]) {
      const sel = $(id);
      const current = sel.value;
      sel.innerHTML = '<option value="">All accounts</option>' +
        state.spondUsers.map((u) => `<option value="${esc(u.id)}">${esc(u.display_name)}</option>`).join("");
      sel.value = current;
    }
  }

  /* ── Views ───────────────────────────────────────────────────────── */
  function showView(view) {
    if (!VIEWS.includes(view)) view = "queue";
    state.view = view;
    for (const v of VIEWS) $(`view-${v}`).hidden = v !== view;
    document.querySelectorAll(".views a").forEach((a) => {
      if (a.dataset.view === view) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    if (view === "queue") renderQueue();
    if (view === "timeline") renderTimeline();
    if (view === "users") loadUsersView();
    if (view === "log") loadLog();
    if (view === "charts") loadCharts();
  }

  /* ── Queue ───────────────────────────────────────────────────────── */
  function renderCounters() {
    const s = state.stats;
    const counts = Core.countByState(state.events.filter((e) => !isPast(e)));
    $("count-armed").textContent = state.jobs.length;
    $("count-open").textContent = counts.open;
    if (!s) return;
    $("count-failed").textContent = s.events_failed;
    $("count-sent").textContent = s.events_processed;
    $("count-users").textContent = s.active_users;
    $("count-p50").textContent = s.rsvp_p50_ms != null ? `${s.rsvp_p50_ms} ms` : "–";
    $("count-p95").textContent = s.rsvp_p95_ms != null ? `${s.rsvp_p95_ms} ms` : "–";
    $("count-p50").title = `Based on ${s.rsvp_sample_count} answers`;
    $("count-sync").innerHTML = s.last_discovery_at
      ? `<span data-rel="${esc(s.last_discovery_at)}">${esc(Core.formatRelative(s.last_discovery_at))}</span>`
      : "Never";
  }

  function renderHero() {
    const now = Date.now();
    const job = state.jobs.filter((j) => Date.parse(j.fire_at) > now).sort((a, b) => Date.parse(a.fire_at) - Date.parse(b.fire_at))[0];
    const cd = $("next-countdown");
    if (!job) {
      cd.textContent = "idle";
      cd.classList.add("idle");
      cd.removeAttribute("data-until");
      $("next-title").textContent = "Nothing is armed.";
      $("next-sub").textContent = "Answers are armed when a member picks Going or Not going.";
      return;
    }
    const ev = eventById(String(job.event_id));
    cd.classList.remove("idle");
    cd.dataset.until = job.fire_at;
    cd.textContent = Core.formatClock(Date.parse(job.fire_at) - now);
    $("next-title").innerHTML = `${esc(job.heading || ev?.heading || "Untitled event")}${ev ? `<span class="mono">→ ${SHORT[ev.user_choice]}</span>` : ""}`;
    $("next-sub").textContent = [
      job.user_name || (ev && nameOf(ev.user_id)),
      `fires ${Core.formatStamp(job.fire_at)}`,
      ev?.start_timestamp ? `event ${Core.formatDay(ev.start_timestamp)} ${Core.formatTime(ev.start_timestamp)}` : null,
    ].filter(Boolean).join(" · ");
  }

  function isPast(e) {
    return e.start_timestamp && Date.parse(e.start_timestamp) < Date.now();
  }

  function toggle(e, disabled) {
    return `<span class="tog" role="group" aria-label="${esc(`Answer for ${e.heading || "event"}`)}">${CHOICES.map((c) =>
      `<button type="button" class="c-${c}" data-choice="${c}" data-id="${esc(e.id)}" aria-label="${L[c]}" title="${L[c]}" aria-pressed="${e.user_choice === c}" ${disabled ? "disabled" : ""}>${SHORT[c]}</button>`,
    ).join("")}</span>`;
  }

  function stateCell(r) {
    const { event: e, state: s, job } = r;
    const label = { open: "no answer", self: "left to member", armed: "armed", sending: "sending", sent: "answered", failed: "failed" }[s];
    let note = "";
    if (s === "armed" && !job) {
      note = e.invite_time && Date.parse(e.invite_time) > Date.now() ? "<small>no timer</small>" : "<small>due now</small>";
    }
    return `<span class="state state-${s}">${label}${note}</span>`;
  }

  function renderQueue() {
    renderHero();
    renderCounters();
    const now = Date.now();
    const account = $("q-account").value;
    const want = $("q-state").value;
    const q = $("q-search").value.trim().toLowerCase();
    const includePast = $("q-past").checked;

    const rows = Core.buildQueue(state.events, state.jobs, state.spondUsers, now).filter((r) =>
      (includePast || !isPast(r.event)) &&
      (!account || r.event.user_id === account) &&
      (!want || r.state === want) &&
      (!q || (r.event.heading || "").toLowerCase().includes(q) || r.userName.toLowerCase().includes(q)),
    );

    if (!rows.length) {
      $("queue-body").innerHTML = `<tr><td colspan="8" class="empty-note">No events match these filters.</td></tr>`;
      return;
    }
    $("queue-body").innerHTML = rows.map((r) => {
      const e = r.event;
      const past = r.state === "sent" || isPast(e);
      const fire = r.job?.fire_at || e.invite_time;
      const until = fire ? Date.parse(fire) - now : null;
      const inCell = until == null ? "–"
        : until > 0 ? `<span class="${until < DAY ? "t-strong" : ""}" data-in="${esc(fire)}">${esc(Core.formatDuration(until))}</span>`
        : `<span class="t-dim">${esc(Core.formatRelative(fire, now))}</span>`;
      const actions = [];
      if (r.job && r.state === "armed") {
        actions.push(`<button type="button" class="btn btn-small" data-fire="${esc(r.job.job_id)}" data-event="${esc(e.id)}">Send now</button>`);
        actions.push(`<button type="button" class="btn btn-small btn-quiet" data-disarm="${esc(r.job.job_id)}" data-event="${esc(e.id)}">Disarm</button>`);
      }
      if (r.state === "failed" && Core.retryPayload(e)) {
        actions.push(`<button type="button" class="btn btn-small btn-sig" data-retry="${esc(e.id)}">Retry</button>`);
      }
      return `
        <tr data-testid="queue-row" class="${past ? "row-past" : ""}">
          <td class="c-fa mono nowrap">${fire ? esc(Core.formatStamp(fire)) : "–"}</td>
          <td class="c-in mono nowrap">${inCell}</td>
          <td class="c-ev"><span class="t-strong">${esc(e.heading || "Untitled event")}</span>
            ${r.state === "failed" && e.error_message ? `<div class="t-err">${esc(e.error_message)}</div>` : ""}</td>
          <td class="c-acc">${esc(r.userName)}</td>
          <td class="c-sa nowrap">${e.start_timestamp ? `${esc(Core.formatDay(e.start_timestamp))} ${esc(Core.formatTime(e.start_timestamp))}` : "–"}</td>
          <td class="c-tog">${toggle(e, r.state === "sent" || r.state === "sending")}</td>
          <td class="c-st">${stateCell(r)}</td>
          <td class="c-act"><div class="actions">${actions.join("")}</div></td>
        </tr>`;
    }).join("");
  }

  async function setChoice(id, choice) {
    const ev = eventById(id);
    if (!ev || ev.user_choice === choice) return;
    const updated = await guarded(() => apiJson(`/events/${id}`, "PATCH", { user_choice: choice }));
    if (!updated) return;
    state.events = state.events.map((e) => (e.id === id ? updated : e));
    toast(`${ev.heading || "Event"} (${nameOf(ev.user_id)}): ${L[choice]}`, "success");
    renderQueue();
    if (state.view === "timeline") renderTimeline();
    loadJobs().then(() => { renderQueue(); if (state.view === "timeline") renderTimeline(); });
  }

  async function retry(id) {
    const ev = eventById(id);
    const payload = ev && Core.retryPayload(ev);
    if (!payload) return;
    const updated = await guarded(() => apiJson(`/events/${id}`, "PATCH", payload));
    if (!updated) return;
    state.events = state.events.map((e) => (e.id === id ? updated : e));
    toast(`${ev.heading || "Event"}: queued for another attempt.`, "success");
    renderQueue();
    loadStats();
    loadJobs().then(renderQueue);
  }

  async function fireNow(jobId, eventId) {
    const ev = eventById(eventId);
    const ok = await confirmAction(
      "Send this answer now?",
      `${ev?.heading || "This event"} for ${nameOf(ev?.user_id)}: SpondBot sends "${L[ev?.user_choice] || "the answer"}" immediately instead of waiting for registration to open. Spond may reject it if registration is still closed.`,
      "Send now",
    );
    if (!ok) return;
    if ((await guarded(() => apiJson(`/admin/scheduler/${encodeURIComponent(jobId)}/fire`, "POST"))) === FAILED) return;
    toast("Sending now. The result appears in the log.", "success");
    setTimeout(refreshQueue, 2500);
    loadJobs().then(renderQueue);
  }

  async function disarm(jobId, eventId) {
    const ev = eventById(eventId);
    const ok = await confirmAction(
      "Disarm this answer?",
      `${ev?.heading || "This event"} keeps its answer, but SpondBot will not send it at the exact opening time. The minute-by-minute fallback can still send it.`,
      "Disarm",
    );
    if (!ok) return;
    if ((await guarded(() => apiJson(`/admin/scheduler/${encodeURIComponent(jobId)}`, "DELETE"))) === FAILED) return;
    toast("Timer removed.", "info");
    await loadJobs();
    renderQueue();
  }

  async function sync() {
    const btn = $("sync-btn");
    btn.disabled = true;
    const done = await guarded(() => apiJson("/admin/sync", "POST"));
    if (done !== FAILED) toast("Sync started. New events appear in a few seconds.", "success");
    setTimeout(() => { btn.disabled = false; refreshQueue(); loadStats(); }, 5000);
  }

  /* ── Timeline ────────────────────────────────────────────────────── */
  function renderTimeline() {
    const now = Date.now();
    const start = new Date(now - 2 * DAY); start.setHours(0, 0, 0, 0);
    const end = new Date(start.getTime() + 16 * DAY);
    const pct = Core.timelineScale(start.getTime(), end.getTime());
    const inRange = (t) => t != null && t >= start.getTime() && t <= end.getTime();

    const days = [];
    for (let t = start.getTime(), i = 0; t < end.getTime(); t += DAY, i++) days.push({ t, i });
    const today = new Date(now); today.setHours(0, 0, 0, 0);
    const ticks = days.map(({ t, i }) => {
      const isToday = t === today.getTime();
      const p = Core.dayParts(t);
      return `<span class="tl-tick${isToday ? " today" : ""}${i % 2 && !isToday ? " odd" : ""}" style="left:${pct(t + DAY / 2)}%">${isToday ? "Today" : `${p.weekday} ${p.day}`}</span>`;
    }).join("");
    const grid = days.map(({ t }) => `<i class="tl-day" style="left:${pct(t)}%"></i>`).join("");

    const lanes = Core.groupByUser(state.events, state.spondUsers);
    if (!lanes.length) {
      $("timeline").innerHTML = '<p class="empty-note">No Spond accounts yet.</p>';
      return;
    }
    $("timeline").innerHTML = `
      <div class="tl-grid">
        <div class="tl-spacer"></div><div class="tl-axis">${ticks}</div>
        ${lanes.map((lane) => {
          const visible = lane.events.filter((e) => inRange(Date.parse(e.invite_time)) || inRange(Date.parse(e.start_timestamp)));
          return `
            <div class="tl-name" data-testid="lane-name"><b>${esc(lane.name)}</b><span>${visible.length} in range</span></div>
            <div class="tl-track" data-testid="lane" aria-label="${esc(lane.name)}">
              <span class="visually-hidden">${esc(lane.name)}</span>
              ${grid}<i class="tl-now" style="left:${pct(now)}%"></i>
              ${visible.map((e) => marker(e, pct, now)).join("")}
            </div>`;
        }).join("")}
      </div>`;
    renderDetail();
  }

  function marker(e, pct, now) {
    const s = Core.eventState(e, now);
    const invite = e.invite_time ? pct(e.invite_time) : null;
    const startP = e.start_timestamp ? pct(e.start_timestamp) : null;
    const label = `${e.heading || "Untitled event"}: registration opens ${e.invite_time ? `${Core.formatDay(e.invite_time)}, ${Core.formatTime(e.invite_time)}` : "unknown"}, answer ${L[e.user_choice]}, ${Core.STATE_LABELS[s]}`;
    const link = invite != null && startP != null
      ? `<i class="tl-link m-${s}" style="left:${invite}%;width:${Math.max(0, startP - invite)}%"></i>` : "";
    const mark = invite != null
      ? `<button type="button" class="tl-mark m-${s}" style="left:${invite}%" data-mark="${esc(e.id)}" aria-label="${esc(label)}" aria-pressed="${state.selectedEvent === e.id}" title="${esc(label)}"></button>` : "";
    const block = startP != null ? `<i class="tl-event c-${e.user_choice}" style="left:${startP}%" title="${esc(`${e.heading}: ${Core.formatDay(e.start_timestamp)} ${Core.formatTime(e.start_timestamp)}`)}"></i>` : "";
    return link + block + mark;
  }

  function renderDetail() {
    const box = $("timeline-detail");
    const e = state.selectedEvent && eventById(state.selectedEvent);
    if (!e) {
      box.innerHTML = '<p class="t-dim">Select a marker to see the event.</p>';
      return;
    }
    const s = Core.eventState(e);
    const when = (v) => (v ? `${Core.formatDay(v)}, ${Core.formatTime(v)}` : "Unknown");
    box.innerHTML = `
      <h2>${esc(e.heading || "Untitled event")}</h2>
      <dl>
        <dt>Account</dt><dd>${esc(nameOf(e.user_id))}</dd>
        <dt>Registration opens</dt><dd class="mono">${esc(when(e.invite_time))}</dd>
        <dt>Event starts</dt><dd class="mono">${esc(when(e.start_timestamp))}</dd>
        <dt>State</dt><dd><span class="state state-${s}">${esc(Core.STATE_LABELS[s])}</span></dd>
        ${e.error_message ? `<dt>Error</dt><dd class="t-err">${esc(e.error_message)}</dd>` : ""}
      </dl>
      <div>${toggle(e, s === "sent" || s === "sending")}</div>`;
  }

  /* ── Users ───────────────────────────────────────────────────────── */
  async function loadUsersView() {
    const accounts = await guarded(() => apiJson("/accounts"));
    if (accounts) state.accounts = accounts;
    renderUsers();
  }

  function renderUsers() {
    $("logins-body").innerHTML = state.accounts.length
      ? state.accounts.map((a) => `
        <tr>
          <td class="t-strong">${esc(a.username)}</td>
          <td>${a.is_admin ? '<span class="badge badge-sig">admin</span>' : '<span class="badge">member</span>'}</td>
          <td>${a.linked_user_id ? esc(nameOf(a.linked_user_id)) : '<span class="t-dim">none</span>'}</td>
          <td><div class="actions">
            <button type="button" class="btn btn-small" data-edit-login="${esc(a.id)}">Edit</button>
            <button type="button" class="btn btn-small btn-danger" data-delete-login="${esc(a.id)}" ${a.id === state.me.sub ? 'disabled title="You cannot delete your own login"' : ""}>Delete</button>
          </div></td>
        </tr>`).join("")
      : '<tr><td colspan="4" class="empty-note">No logins.</td></tr>';

    $("spond-body").innerHTML = state.spondUsers.length
      ? state.spondUsers.map((u) => `
        <tr>
          <td class="t-strong">${esc(u.display_name)}</td>
          <td>${esc(u.login)}</td>
          <td class="mono t-dim">${u.profile_id ? esc(u.profile_id.slice(0, 10)) : "–"}</td>
          <td><input type="checkbox" class="switch" role="switch" aria-label="Active" data-active="${esc(u.id)}" ${u.is_active ? "checked" : ""} /></td>
          <td><div class="actions"><button type="button" class="btn btn-small btn-danger" data-delete-spond="${esc(u.id)}">Delete</button></div></td>
        </tr>`).join("")
      : '<tr><td colspan="5" class="empty-note">No Spond accounts connected.</td></tr>';
  }

  function openLoginDialog(account) {
    state.editingLogin = account || null;
    const editing = Boolean(account);
    $("login-dialog-title").textContent = editing ? `Edit ${account.username}` : "Add login";
    $("login-submit").textContent = editing ? "Save" : "Create login";
    document.querySelector("#login-dialog [data-create]").hidden = editing;
    $("login-password-label").textContent = editing ? "New password (leave empty to keep)" : "Password";
    $("login-username").value = "";
    $("login-password").value = "";
    $("login-linked").innerHTML = '<option value="">None</option>' +
      state.spondUsers.map((u) => `<option value="${esc(u.id)}">${esc(u.display_name)} (${esc(u.login)})</option>`).join("");
    $("login-linked").value = account?.linked_user_id || "";
    $("login-admin").checked = Boolean(account?.is_admin);
    openDialog("login-dialog");
  }

  async function submitLogin(ev) {
    ev.preventDefault();
    const editing = state.editingLogin;
    const username = $("login-username").value.trim();
    const password = $("login-password").value;
    const linked_user_id = $("login-linked").value || null;
    const is_admin = $("login-admin").checked;
    if (!editing && !username) return showDialogError("login-dialog", "Enter a username.");
    if ((!editing || password) && password.length < 8) return showDialogError("login-dialog", "The password needs at least 8 characters.");
    try {
      if (editing) {
        const body = { is_admin, linked_user_id };
        if (password) body.new_password = password;
        await apiJson(`/accounts/${editing.id}`, "PATCH", body);
        toast(`${editing.username} updated.`, "success");
      } else {
        await apiJson("/accounts", "POST", { username, password, linked_user_id, is_admin });
        toast(`Login ${username} created.`, "success");
      }
      closeDialog("login-dialog");
      loadUsersView();
    } catch (err) {
      showDialogError("login-dialog", err.message);
    }
  }

  async function deleteLogin(id) {
    const a = state.accounts.find((x) => x.id === id);
    if (!a) return;
    const ok = await confirmAction(`Delete login ${a.username}?`, "They will no longer be able to sign in. Their Spond account and events are kept.", "Delete login");
    if (!ok) return;
    if ((await guarded(() => apiJson(`/accounts/${id}`, "DELETE"))) !== FAILED) {
      toast(`Login ${a.username} deleted.`, "success");
      loadUsersView();
    }
  }

  async function toggleActive(id, active, input) {
    try {
      const updated = await apiJson(`/spond-accounts/${id}`, "PATCH", { is_active: active });
      state.spondUsers = state.spondUsers.map((u) => (u.id === id ? updated : u));
      toast(`${updated.display_name} ${active ? "resumed" : "paused"}.`, "success");
      loadStats();
    } catch (err) {
      input.checked = !active;
      toast(err.message, "error", 6000);
    }
  }

  async function deleteSpond(id) {
    const u = state.spondUsers.find((x) => x.id === id);
    if (!u) return;
    const ok = await confirmAction(`Delete ${u.display_name}?`, "This removes the Spond account and all of its events from SpondBot. Logins linked to it stay, without a linked account.", "Delete account");
    if (!ok) return;
    if ((await guarded(() => apiJson(`/spond-accounts/${id}`, "DELETE"))) !== FAILED) {
      state.spondUsers = state.spondUsers.filter((x) => x.id !== id);
      state.events = state.events.filter((e) => e.user_id !== id);
      fillAccountSelects();
      renderUsers();
      toast(`${u.display_name} deleted.`, "success");
      loadStats();
    }
  }

  function openSpondDialog() {
    for (const id of ["spond-login", "spond-password", "spond-name"]) $(id).value = "";
    openDialog("spond-dialog");
  }

  async function submitSpond(ev) {
    ev.preventDefault();
    const login = $("spond-login").value.trim();
    const password = $("spond-password").value;
    const display_name = $("spond-name").value.trim() || login.split("@")[0];
    if (!login || !password) return showDialogError("spond-dialog", "Enter the Spond login and password.");
    const btn = $("spond-submit");
    btn.disabled = true;
    btn.textContent = "Checking with Spond…";
    try {
      await apiJson("/spond-accounts", "POST", { login, password, display_name });
      closeDialog("spond-dialog");
      toast(`${display_name} connected.`, "success");
      await loadSpondUsers();
      renderUsers();
      loadStats();
    } catch (err) {
      showDialogError("spond-dialog", err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Connect";
    }
  }

  /* ── Log ─────────────────────────────────────────────────────────── */
  async function loadLog() {
    const account = $("log-account").value;
    const rows = await guarded(() => apiJson(`/admin/rsvp-log?limit=200${account ? `&user_id=${encodeURIComponent(account)}` : ""}`));
    if (!rows) return;
    const outcome = $("log-outcome").value;
    const shown = outcome ? rows.filter((r) => r.outcome === outcome) : rows;
    const badge = { success: '<span class="badge badge-ok">succeeded</span>', retry_success: '<span class="badge badge-sig">after retry</span>' };
    $("log-body").innerHTML = shown.length
      ? shown.map((r) => {
          const ev = eventById(r.event_id);
          const latency = r.submitted_at && ev?.invite_time ? Date.parse(r.submitted_at) - Date.parse(ev.invite_time) : null;
          return `
            <tr>
              <td class="mono nowrap">${esc(Core.formatStamp(r.fired_at))}</td>
              <td>${esc(nameOf(r.user_id))}</td>
              <td>${ev ? `<span class="t-strong">${esc(ev.heading || "Untitled event")}</span>` : `<span class="mono t-dim">${esc(r.spond_event_id.slice(0, 12))}</span>`}</td>
              <td>${esc(L[r.choice] || r.choice)}</td>
              <td>${badge[r.outcome] || '<span class="badge badge-bad">failed</span>'}</td>
              <td class="num mono">${latency != null ? `${latency} ms` : "–"}</td>
              <td class="num mono">${r.retry_count}</td>
              <td>${r.error_detail ? `<span class="t-err">${esc(r.error_detail)}</span>` : ""}</td>
            </tr>`;
        }).join("")
      : '<tr><td colspan="8" class="empty-note">No answers logged for these filters yet.</td></tr>';
  }

  /* ── Charts ──────────────────────────────────────────────────────── */
  async function loadCharts() {
    const days = $("chart-days").value;
    const account = $("chart-account").value;
    const data = await guarded(() => apiJson(`/admin/charts?days=${days}${account ? `&user_id=${encodeURIComponent(account)}` : ""}`));
    if (!data) return;

    $("per-account-body").innerHTML = data.per_user.length
      ? data.per_user.map((u) => `
        <tr>
          <td>${esc(u.user_name)}</td>
          <td class="num mono">${u.total}</td>
          <td class="num mono">${u.success}</td>
          <td class="num mono">${u.p50_ms != null ? `${u.p50_ms} ms` : "–"}</td>
          <td class="num mono">${u.p95_ms != null ? `${u.p95_ms} ms` : "–"}</td>
        </tr>`).join("")
      : '<tr><td colspan="5" class="empty-note">No answers in this range.</td></tr>';

    if (typeof window.Chart === "undefined") {
      $("chart-note").hidden = false;
      return;
    }
    drawCharts(data);
  }

  function drawCharts(data) {
    const css = getComputedStyle(document.documentElement);
    const token = (n) => css.getPropertyValue(n).trim();
    const grid = token("--line");
    const text = token("--dim");
    const surface = token("--panel");
    const font = { family: token("--mono") || "monospace", size: 11 };
    const axes = (extra = {}) => ({
      grid: { color: grid, drawTicks: false },
      border: { display: false },
      ticks: { color: text, font, padding: 6 },
      ...extra,
    });

    for (const c of Object.values(state.charts)) c.destroy();
    const animation = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : { duration: 300 };

    state.charts.latency = new Chart($("chart-latency"), {
      type: "scatter",
      data: {
        datasets: [{
          label: "Latency",
          data: data.latency_scatter.map((p) => ({ x: Date.parse(p.fired_at), y: p.latency_ms, user: p.user_name, heading: p.heading })),
          backgroundColor: token("--sig"),
          borderColor: surface,
          borderWidth: 2,
          pointRadius: 5,
          pointHoverRadius: 7,
          pointHitRadius: 10,
        }],
      },
      options: {
        animation,
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: axes({ type: "linear", ticks: { color: text, font, maxTicksLimit: 4, callback: (v) => Core.formatDay(v) } }),
          y: axes({ beginAtZero: true, title: { display: true, text: "ms", color: text, font } }),
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => `${ctx.raw.user} · ${ctx.raw.heading || "event"}: ${ctx.parsed.y} ms (${Core.formatDay(ctx.parsed.x)} ${Core.formatTime(ctx.parsed.x)})`,
            },
          },
        },
      },
    });

    const series = [
      ["Succeeded", "success", "--chart-success"],
      ["After retry", "retry_success", "--chart-retry"],
      ["Failed", "failed", "--chart-failed"],
    ];
    state.charts.daily = new Chart($("chart-daily"), {
      type: "bar",
      data: {
        labels: data.daily_success_rate.map((d) => Core.formatDay(`${d.date}T12:00:00`)),
        datasets: series.map(([label, key, color]) => ({
          label,
          data: data.daily_success_rate.map((d) => d[key]),
          backgroundColor: token(color),
          borderColor: surface,
          borderWidth: { top: 2 },
          borderRadius: 3,
          borderSkipped: "bottom",
          maxBarThickness: 28,
        })),
      },
      options: {
        animation,
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: axes({ stacked: true, grid: { display: false } }),
          y: axes({ stacked: true, beginAtZero: true, ticks: { color: text, font, precision: 0 } }),
        },
        plugins: { legend: { display: false }, tooltip: { mode: "index", intersect: false } },
      },
    });
  }

  /* ── Wiring ──────────────────────────────────────────────────────── */
  function tick() {
    const cd = $("next-countdown");
    if (cd.dataset.until) {
      const left = Date.parse(cd.dataset.until) - Date.now();
      if (left <= 0) { cd.removeAttribute("data-until"); refreshQueue(); }
      else cd.textContent = Core.formatClock(left);
    }
  }

  function wire() {
    bindMenu($("account-btn"), $("account-menu"));
    wireDialogs();
    $("menu-signout").addEventListener("click", signOut);
    $("sync-btn").addEventListener("click", sync);
    window.addEventListener("hashchange", () => showView(location.hash.slice(1)));

    for (const id of ["q-account", "q-state", "q-past"]) $(id).addEventListener("change", renderQueue);
    $("q-search").addEventListener("input", renderQueue);
    for (const id of ["log-account", "log-outcome"]) $(id).addEventListener("change", loadLog);
    for (const id of ["chart-days", "chart-account"]) $(id).addEventListener("change", loadCharts);

    $("add-login-btn").addEventListener("click", () => openLoginDialog(null));
    $("add-spond-btn").addEventListener("click", openSpondDialog);
    $("login-form").addEventListener("submit", submitLogin);
    $("spond-form").addEventListener("submit", submitSpond);

    document.querySelector(".main").addEventListener("click", (e) => {
      const t = (sel) => e.target.closest(sel);
      let el;
      if ((el = t("[data-choice]"))) return setChoice(el.dataset.id, el.dataset.choice);
      if ((el = t("[data-retry]"))) return retry(el.dataset.retry);
      if ((el = t("[data-fire]"))) return fireNow(el.dataset.fire, el.dataset.event);
      if ((el = t("[data-disarm]"))) return disarm(el.dataset.disarm, el.dataset.event);
      if ((el = t("[data-mark]"))) {
        state.selectedEvent = el.dataset.mark;
        document.querySelectorAll("[data-mark]").forEach((m) => m.setAttribute("aria-pressed", String(m === el)));
        return renderDetail();
      }
      if ((el = t("[data-edit-login]"))) return openLoginDialog(state.accounts.find((a) => a.id === el.dataset.editLogin));
      if ((el = t("[data-delete-login]"))) return deleteLogin(el.dataset.deleteLogin);
      if ((el = t("[data-delete-spond]"))) return deleteSpond(el.dataset.deleteSpond);
    });
    document.querySelector(".main").addEventListener("change", (e) => {
      const sw = e.target.closest("[data-active]");
      if (sw) toggleActive(sw.dataset.active, sw.checked, sw);
    });

    setInterval(tick, 1000);
    setInterval(() => document.querySelectorAll("[data-rel]").forEach((el) => { el.textContent = Core.formatRelative(el.dataset.rel); }), 30e3);
    setInterval(() => { if (state.view === "queue") renderQueue(); }, 60e3);
    setInterval(loadStats, 30e3);
    setInterval(refreshQueue, 5 * 60e3);
  }

  async function init() {
    state.me = await requireAdmin();
    $("account-btn").textContent = initials(state.me.username);
    wire();
    await loadSpondUsers();
    await Promise.all([loadEvents(), loadJobs(), loadStats()]);
    showView(location.hash.slice(1) || "queue");

    connectStream("/admin/stream", {
      rsvp_fired: (d) => {
        toast(`Answer ${d.outcome === "success" ? "sent" : "failed"}: ${d.heading} (${L[d.choice] || d.choice})`, d.outcome === "success" ? "success" : "error");
        refreshQueue();
        loadStats();
        if (state.view === "log") loadLog();
      },
      discovery_completed: () => { refreshQueue(); loadStats(); },
      scheduler_changed: () => loadJobs().then(() => { if (state.view === "queue") renderQueue(); }),
    }, $("live"));
  }

  init();
})();
