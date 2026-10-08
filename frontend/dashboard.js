/**
 * Member dashboard.
 *
 * Layout: summary line → failed answers (with Retry) → decision inbox (one
 * undecided event at a time) next to a day-grouped agenda with a segmented
 * Going / Not going / Leave to me control per event.
 *
 * Loading: every upcoming event up front; the Past tab only when it is opened, newest first,
 * 30 at a time ("Load older"), so a long history never slows the page down.
 *
 * Depends on core.js (Core) and app.js (api helpers, dialogs, toasts).
 */
(function () {
  const L = Core.CHOICE_LABELS;
  const CHOICES = ["accept", "decline", "manual"];
  const RELOAD_MS = 5 * 60e3;
  const PAST_PAGE = 30;

  const state = {
    me: null,
    spondUser: null,
    events: [],          // upcoming + the past pages loaded so far
    upcoming: [],
    past: { items: [], hasMore: false, loading: false, loaded: false },
    loaded: false,
    tab: "upcoming",
    inboxIndex: 0,
    busy: new Set(),
    prefs: null,
  };

  const $ = (id) => document.getElementById(id);
  const rel = (v) => `<span data-rel="${esc(v)}">${esc(Core.formatRelative(v))}</span>`;
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  /* ── Data ────────────────────────────────────────────────────────── */
  const query = (o) => new URLSearchParams(o).toString();

  function rebuildEvents() {
    const seen = new Set();
    state.events = [...state.upcoming, ...state.past.items].filter((e) => !seen.has(e.id) && seen.add(e.id));
  }

  /** Everything that has not started yet (no limit: a member's upcoming list is short). */
  async function loadEvents() {
    try {
      state.upcoming = await apiJson(`/events?${query({ start_from: new Date().toISOString(), order: "start" })}`);
      rebuildEvents();
      state.loaded = true;
      render();
    } catch (err) {
      if (err.status === 401) { window.location.href = "/"; return; }
      $("headline").textContent = "Could not load your events";
      $("subline").textContent = err.message;
    }
  }

  /** Events that already started, newest first. The first page comes with the first visit to the tab. */
  async function loadPast(more = false) {
    if (state.past.loading || (!more && state.past.loaded)) return;
    state.past.loading = true;
    render();
    try {
      const page = await apiJson(`/events?${query({
        start_to: new Date().toISOString(), order: "-start", limit: PAST_PAGE, offset: state.past.items.length,
      })}`);
      state.past.items = [...state.past.items, ...page];
      state.past.hasMore = page.length === PAST_PAGE;
      state.past.loaded = true;
      rebuildEvents();
    } catch (err) {
      if (err.status === 401) { window.location.href = "/"; return; }
      toast(`Could not load past events. ${err.message}`, "error", 6000);
    } finally {
      state.past.loading = false;
      render();
    }
  }

  function replaceEvent(updated) {
    const swap = (list) => list.map((e) => (e.id === updated.id ? updated : e));
    state.upcoming = swap(state.upcoming);
    state.past.items = swap(state.past.items);
    rebuildEvents();
  }

  async function setChoice(id, choice) {
    const ev = state.events.find((e) => e.id === id);
    if (!ev || state.busy.has(id) || ev.user_choice === choice) return;
    state.busy.add(id);
    render();
    try {
      replaceEvent(await apiJson(`/events/${id}`, "PATCH", { user_choice: choice }));
      toast(`${ev.heading || "Event"}: ${L[choice]}`, "success");
    } catch (err) {
      if (err.status === 401) { window.location.href = "/"; return; }
      toast(`Could not save your answer. ${err.message}`, "error", 6000);
    } finally {
      state.busy.delete(id);
      render();
    }
  }

  async function retry(id) {
    const ev = state.events.find((e) => e.id === id);
    const payload = ev && Core.retryPayload(ev);
    if (!payload) return;
    state.busy.add(id);
    render();
    try {
      replaceEvent(await apiJson(`/events/${id}`, "PATCH", payload));
      toast(`${ev.heading || "Event"}: SpondBot will try again.`, "success");
    } catch (err) {
      toast(`Retry failed. ${err.message}`, "error", 6000);
    } finally {
      state.busy.delete(id);
      render();
    }
  }

  /* ── Rendering ───────────────────────────────────────────────────── */
  function render() {
    if (!state.me) return;
    if (!state.me.linked_user_id) return renderUnlinked();
    if (!state.loaded) return;

    const now = Date.now();
    const open = Core.needsAnswer(state.events, now);
    const { upcoming, past } = Core.splitByTime(state.events, now);

    renderSummary(open, upcoming, now);
    renderFailures(upcoming, now);
    renderInbox(open);
    renderAgenda(state.tab === "past" ? past : upcoming, now);
  }

  function renderUnlinked() {
    $("headline").textContent = "No Spond account is linked to your login";
    $("subline").textContent = "Connect your Spond account below and your events will show up here.";
    $("connect").hidden = false;
    $("inbox").hidden = true;
    $("agenda").hidden = true;
  }

  async function connectAccount(e) {
    e.preventDefault();
    const err = $("connect-error");
    err.hidden = true;
    const login = $("connect-login").value.trim();
    const password = $("connect-password").value;
    const name = $("connect-name").value.trim();
    if (!login || !password) {
      err.textContent = "Enter your Spond email or phone and password.";
      err.hidden = false;
      return;
    }
    const btn = $("connect-submit");
    btn.disabled = true;
    btn.textContent = "Checking with Spond…";
    try {
      state.spondUser = await apiJson("/spond-accounts/me", "POST", { login, password, display_name: name || null });
      // The server issued a fresh session cookie that includes the new link.
      const fresh = await fetchCurrentUser();
      state.me = { ...state.me, ...fresh, linked_user_id: state.spondUser.id };
      $("connect").hidden = true;
      paintIdentity();
      toast("Spond account connected.", "success");
      await loadEvents();
      startStream();
    } catch (ex) {
      err.textContent = ex.status === 429 ? "Too many attempts. Wait a minute and try again." : ex.message;
      err.hidden = false;
      btn.disabled = false;
      btn.textContent = "Connect";
    }
  }

  function renderSummary(open, upcoming, now) {
    const next = Core.nextAnswer(state.events, now);
    if (open.length) {
      $("headline").textContent = `${plural(open.length, "event")} need${open.length === 1 ? "s" : ""} an answer`;
    } else if (upcoming.length) {
      $("headline").textContent = "Every upcoming event has an answer";
    } else {
      $("headline").textContent = "No upcoming events";
    }
    $("subline").innerHTML = next
      ? `Next automatic answer: <b>${esc(next.heading || "Untitled event")}</b>, ${rel(next.invite_time)}.`
      : upcoming.length
        ? "No automatic answers are scheduled."
        : "New events from Spond show up here automatically.";
  }

  function renderFailures(upcoming, now) {
    const failed = upcoming.filter((e) => Core.eventState(e, now) === "failed");
    $("failures").innerHTML = failed.map((e) => {
      const canRetry = Core.retryPayload(e) !== null;
      return `
        <div class="failure" data-testid="failure">
          <p><b>${esc(e.heading || "Untitled event")}</b>: SpondBot could not send your answer (${esc(L[e.user_choice])}).
            <span>${esc(e.error_message || "Spond did not accept the request.")}</span></p>
          ${canRetry
            ? `<button class="btn" type="button" data-retry="${esc(e.id)}" ${state.busy.has(e.id) ? "disabled" : ""}>Retry</button>`
            : "<span>Answer it in the Spond app.</span>"}
        </div>`;
    }).join("");
  }

  function renderInbox(open) {
    const inbox = $("inbox");
    inbox.hidden = open.length === 0;
    $("layout").classList.toggle("has-inbox", open.length > 0);
    if (!open.length) return;
    if (state.inboxIndex >= open.length) state.inboxIndex = 0;
    const e = open[state.inboxIndex];
    const p = e.start_timestamp ? Core.dayParts(e.start_timestamp) : null;
    const busy = state.busy.has(e.id) ? "disabled" : "";
    const when = e.invite_time
      ? `Registration opens ${esc(Core.formatDay(e.invite_time))} at ${esc(Core.formatTime(e.invite_time))}, ${rel(e.invite_time)}.`
      : "The registration time is not known yet.";

    $("inbox-card").innerHTML = `
      <article class="decision">
        ${p ? `<div class="decision-date"><b class="num">${p.day}</b><span>${p.weekday}<small>${p.month} · ${esc(Core.formatTime(e.start_timestamp))}</small></span></div>` : ""}
        <h3>${esc(e.heading || "Untitled event")}</h3>
        <p>${when} Pick an answer and SpondBot sends it the second registration opens.</p>
        <div class="decision-actions">
          <button type="button" class="btn-going" data-choice="accept" data-id="${esc(e.id)}" ${busy}>Going</button>
          <button type="button" class="btn-notgoing" data-choice="decline" data-id="${esc(e.id)}" ${busy}>Not going</button>
        </div>
        <div class="decision-foot">
          <span>${state.inboxIndex + 1} of ${open.length}</span>
          ${open.length > 1 ? '<button type="button" data-later>Decide later</button>' : "<span>Or leave it and answer in Spond</span>"}
        </div>
      </article>`;
  }

  function stateLine(e, s) {
    switch (s) {
      case "open":
        return e.invite_time
          ? `No answer set · registration opens ${esc(Core.formatDay(e.invite_time))}, ${esc(Core.formatTime(e.invite_time))}`
          : "No answer set";
      case "self": return "Registration is open · answer in the Spond app";
      case "armed":
        return e.invite_time && Date.parse(e.invite_time) > Date.now()
          ? `Scheduled · answers ${rel(e.invite_time)}`
          : "Scheduled · sending shortly";
      case "sending": return "Sending now";
      case "sent": return e.invite_time ? `Sent ${esc(Core.formatDay(e.invite_time))}, ${esc(Core.formatTime(e.invite_time))}` : "Sent";
      case "failed": return "Could not be sent";
      default: return "";
    }
  }

  function controls(e, s, isPast) {
    if (s === "sent") return `<div class="answered">Answered: <b>${esc(L[e.user_choice])}</b></div>`;
    if (isPast) return `<div class="answered">${s === "failed" ? "Failed" : "Your choice"}: <b>${esc(L[e.user_choice])}</b></div>`;
    const disabled = state.busy.has(e.id) || s === "sending" ? "disabled" : "";
    const label = esc(`Answer for ${e.heading || "event"}`);
    return `<div class="seg" role="group" aria-label="${label}">${CHOICES.map((c) =>
      `<button type="button" class="c-${c}" data-choice="${c}" data-id="${esc(e.id)}" aria-pressed="${e.user_choice === c}" ${disabled}>${L[c]}</button>`,
    ).join("")}</div>`;
  }

  function renderAgenda(list, now) {
    $("agenda").hidden = false;
    const isPast = state.tab === "past";
    if (isPast && !state.past.loaded) {
      $("agenda-list").innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
      return;
    }
    if (!list.length) {
      $("agenda-list").innerHTML = isPast
        ? '<div class="empty"><b>No past events</b>Events move here once they have started.</div>'
        : '<div class="empty"><b>No upcoming events</b>SpondBot checks Spond for new events regularly. They appear here with no answer set.</div>';
      return;
    }
    $("agenda-list").innerHTML = Core.groupByDay(list).map((g) => {
      const p = g.date ? Core.dayParts(g.date) : null;
      const label = p
        ? `<span class="wd">${p.weekday}</span><span class="d num">${p.day}</span><span class="mo">${p.month}</span>`
        : '<span class="wd">No date</span>';
      return `
        <section class="day" data-testid="agenda-day">
          <div class="day-label" data-testid="day-label">${label}</div>
          <div class="day-rows">${g.events.map((e) => {
            const s = Core.eventState(e, now);
            return `
              <div class="row" data-testid="agenda-row">
                <div>
                  <h3 class="row-title">${esc(e.heading || "Untitled event")}</h3>
                  ${e.start_timestamp ? `<div class="row-time num">${esc(Core.formatTime(e.start_timestamp))}</div>` : ""}
                  <div class="row-state st-${s}">${stateLine(e, s)}</div>
                </div>
                ${controls(e, s, isPast)}
              </div>`;
          }).join("")}</div>
        </section>`;
    }).join("") + (isPast && state.past.hasMore
      ? `<div class="load-more"><button type="button" class="btn" data-more data-testid="past-more" ${state.past.loading ? "disabled" : ""}>${state.past.loading ? "Loading…" : "Load older events"}</button></div>`
      : "");
  }

  function refreshRelativeTimes() {
    document.querySelectorAll("[data-rel]").forEach((el) => {
      el.textContent = Core.formatRelative(el.dataset.rel);
    });
  }

  /* ── Tabs ────────────────────────────────────────────────────────── */
  function selectTab(name) {
    state.tab = name;
    for (const t of ["upcoming", "past"]) {
      const tab = $(`tab-${t}`);
      tab.setAttribute("aria-selected", String(t === name));
      tab.tabIndex = t === name ? 0 : -1;
    }
    $("agenda-list").setAttribute("aria-labelledby", `tab-${name}`);
    render();
    if (name === "past") loadPast();
  }

  /* ── Account dialogs ─────────────────────────────────────────────── */
  function showProfile() {
    $("profile-username").value = state.me.username;
    const linked = Boolean(state.spondUser);
    document.querySelectorAll("#profile-dialog [data-linked]").forEach((el) => { el.hidden = !linked; });
    if (linked) {
      $("profile-name").value = state.spondUser.display_name;
      $("profile-login").value = state.spondUser.login;
    }
    openDialog("profile-dialog");
  }

  async function saveProfile(e) {
    e.preventDefault();
    const name = $("profile-name").value.trim();
    if (!name) return showDialogError("profile-dialog", "Enter a display name.");
    try {
      state.spondUser = await apiJson(`/spond-accounts/${state.spondUser.id}`, "PATCH", { display_name: name });
      paintIdentity();
      closeDialog("profile-dialog");
      toast("Profile updated.", "success");
    } catch (err) {
      showDialogError("profile-dialog", err.message);
    }
  }

  function showPassword() {
    for (const id of ["pw-current", "pw-new", "pw-repeat"]) $(id).value = "";
    openDialog("password-dialog");
  }

  async function savePassword(e) {
    e.preventDefault();
    const current = $("pw-current").value;
    const next = $("pw-new").value;
    const repeat = $("pw-repeat").value;
    if (!current || !next || !repeat) return showDialogError("password-dialog", "Fill in all three fields.");
    if (next.length < 8) return showDialogError("password-dialog", "The new password needs at least 8 characters.");
    if (next !== repeat) return showDialogError("password-dialog", "The new passwords do not match.");
    try {
      await apiJson("/auth/me/password", "PATCH", { current_password: current, new_password: next });
      closeDialog("password-dialog");
      toast("Password updated.", "success");
    } catch (err) {
      showDialogError("password-dialog", err.message);
    }
  }

  function showSpondPassword() {
    $("spond-password-login").textContent = state.spondUser?.login || "your Spond account";
    $("spond-password-new").value = "";
    openDialog("spond-password-dialog");
  }

  async function saveSpondPassword(e) {
    e.preventDefault();
    const password = $("spond-password-new").value;
    if (!password) return showDialogError("spond-password-dialog", "Enter your new Spond password.");
    const btn = $("spond-password-submit");
    btn.disabled = true;
    btn.textContent = "Checking with Spond…";
    try {
      await apiJson(`/spond-accounts/${state.me.linked_user_id}/password`, "PUT", { password });
      closeDialog("spond-password-dialog");
      toast("Spond password updated. SpondBot will use it from now on.", "success");
    } catch (err) {
      showDialogError("spond-password-dialog", err.status === 429 ? "Too many attempts. Wait a minute and try again." : err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Update";
    }
  }

  /* ── Notifications & install ─────────────────────────────────────── */
  let pushBusy = false;

  async function paintNotifications() {
    const s = await SpondPush.state();
    const status = $("push-status");
    status.textContent = s.message;
    status.dataset.kind = s.kind;
    $("push-enable").hidden = !s.canEnable;
    $("push-disable").hidden = !s.canDisable;
    $("push-test").hidden = !s.canTest;
    await paintPreferences(s.kind);
    return s;
  }

  /* Which notifications to get: shown once this device can get them (on) or could (off). */
  async function paintPreferences(kind) {
    const form = $("push-prefs");
    form.hidden = !["on", "off"].includes(kind);
    if (form.hidden) return;
    if (!state.prefs) {
      try {
        state.prefs = await SpondPush.preferences();
      } catch {
        form.hidden = true; // no settings, no half-working form
        return;
      }
    }
    renderPreferences();
  }

  function renderPreferences() {
    $("push-prefs-list").innerHTML = Core.notificationGroups().map((g) => `
      <fieldset class="pref-group">
        <legend>${esc(g.group)}</legend>
        ${g.kinds.map((k) => `
          <label class="pref-item" for="pref-${esc(k.key)}">
            <input type="checkbox" id="pref-${esc(k.key)}" data-pref="${esc(k.key)}" ${state.prefs[k.key] ? "checked" : ""} />
            <span><b>${esc(k.label)}</b><small>${esc(k.hint)}</small></span>
          </label>`).join("")}
      </fieldset>`).join("");
  }

  let prefsSavedTimer = null;

  /** Saves right away; if the server refuses, the switch goes back and the dialog says why. */
  async function savePreference(input) {
    const boxes = [...document.querySelectorAll("[data-pref]")];
    const next = { ...state.prefs, [input.dataset.pref]: input.checked };
    boxes.forEach((b) => { b.disabled = true; });
    $("notifications-dialog").querySelector("[data-error]").hidden = true;
    try {
      state.prefs = await SpondPush.savePreferences(next);
      const saved = $("push-prefs-saved");
      saved.textContent = "Saved.";
      clearTimeout(prefsSavedTimer);
      prefsSavedTimer = setTimeout(() => { saved.textContent = ""; }, 2500);
    } catch (err) {
      input.checked = !input.checked;
      showDialogError("notifications-dialog", `Could not save: ${err.message}`);
    } finally {
      boxes.forEach((b) => { b.disabled = false; });
    }
  }

  async function showNotifications() {
    $("push-status").textContent = "Checking this device…";
    for (const id of ["push-enable", "push-disable", "push-test"]) $(id).hidden = true;
    openDialog("notifications-dialog");
    await paintNotifications();
  }

  /** Runs one notification action with the buttons locked; errors show in the dialog. */
  async function pushAction(fn, done) {
    if (pushBusy) return;
    pushBusy = true;
    const buttons = ["push-enable", "push-disable", "push-test"].map($);
    buttons.forEach((b) => { b.disabled = true; });
    $("notifications-dialog").querySelector("[data-error]").hidden = true;
    try {
      const result = await fn();
      await paintNotifications();
      if (done) toast(done(result), "success");
    } catch (err) {
      showDialogError("notifications-dialog", err.status === 429 ? "Too many tests. Wait a minute and try again." : err.message);
      await paintNotifications();
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
      pushBusy = false;
    }
  }

  const enableNotifications = () => pushAction(SpondPush.enable, () => "Notifications are on for this device.");
  const disableNotifications = () => pushAction(SpondPush.disable, () => "Notifications are off for this device.");
  const testNotification = () => pushAction(SpondPush.test, (r) =>
    r.delivered > 0 ? "Test sent. It should arrive in a moment." : "The test could not be delivered. Try turning notifications off and on again.");

  async function paintNotificationsMenu() {
    // Hidden only when the server has no push key; everything else is explained in the dialog.
    $("menu-notifications").hidden = (await SpondPush.state()).kind === "unavailable";
  }

  function paintInstallMenu() {
    $("menu-install").hidden = !["prompt", "ios-steps"].includes(Pwa.mode());
  }

  async function installApp() {
    if (Pwa.mode() === "prompt") await Pwa.prompt();
    else openDialog("install-dialog");
  }

  function paintIdentity() {
    $("menu-spond-password").hidden = !state.me.linked_user_id;
    const name = state.spondUser?.display_name || state.me.username;
    $("account-btn").textContent = initials(name);
    $("menu-name").textContent = name;
    $("menu-sub").textContent = state.me.is_admin ? "Administrator" : state.me.username;
  }

  /* ── Wiring ──────────────────────────────────────────────────────── */
  function wire() {
    bindMenu($("account-btn"), $("account-menu"));
    wireDialogs();
    $("menu-profile").addEventListener("click", showProfile);
    $("menu-password").addEventListener("click", showPassword);
    $("menu-spond-password").addEventListener("click", showSpondPassword);
    $("menu-notifications").addEventListener("click", showNotifications);
    $("menu-install").addEventListener("click", installApp);
    $("push-enable").addEventListener("click", enableNotifications);
    $("push-disable").addEventListener("click", disableNotifications);
    $("push-test").addEventListener("click", testNotification);
    $("push-prefs").addEventListener("change", (e) => {
      if (e.target.matches("[data-pref]")) savePreference(e.target);
    });
    Pwa.onChange(paintInstallMenu);
    $("spond-password-form").addEventListener("submit", saveSpondPassword);
    $("menu-signout").addEventListener("click", signOut);
    $("profile-form").addEventListener("submit", saveProfile);
    $("password-form").addEventListener("submit", savePassword);
    $("connect-form").addEventListener("submit", connectAccount);

    $("tab-upcoming").addEventListener("click", () => selectTab("upcoming"));
    $("tab-past").addEventListener("click", () => selectTab("past"));
    document.querySelector('[role="tablist"]').addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const next = state.tab === "upcoming" ? "past" : "upcoming";
      selectTab(next);
      $(`tab-${next}`).focus();
    });

    document.querySelector(".page").addEventListener("click", (e) => {
      const choice = e.target.closest("[data-choice]");
      if (choice) return setChoice(choice.dataset.id, choice.dataset.choice);
      if (e.target.closest("[data-more]")) return loadPast(true);
      const retryBtn = e.target.closest("[data-retry]");
      if (retryBtn) return retry(retryBtn.dataset.retry);
      if (e.target.closest("[data-later]")) {
        state.inboxIndex += 1;
        render();
      }
    });

    setInterval(refreshRelativeTimes, 30e3);
    setInterval(loadEvents, RELOAD_MS);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") loadEvents();
    });
  }

  async function init() {
    state.me = await requireAuth();
    $("menu-admin").hidden = !state.me.is_admin;
    wire();
    paintIdentity();
    paintInstallMenu();
    paintNotificationsMenu();

    if (!state.me.linked_user_id) return render();

    try {
      state.spondUser = await apiJson(`/spond-accounts/${state.me.linked_user_id}`);
      paintIdentity();
    } catch {
      /* the name falls back to the username */
    }
    await loadEvents();
    startStream();
  }

  function startStream() {
    connectStream("/user/stream", {
      rsvp_fired: (d) => {
        toast(
          d.outcome === "success"
            ? `Answer sent for ${d.heading}: ${L[d.choice] || d.choice}`
            : `SpondBot could not answer ${d.heading}.`,
          d.outcome === "success" ? "success" : "error",
        );
        loadEvents();
      },
    }, $("live"));
  }

  init();
})();
