/**
 * Offline page behaviour: probe /api/v1/health and reload when the server answers.
 * Precached by the service worker; kept in its own file so the Content-Security-Policy can forbid inline scripts.
 */
(function () {
  var chip = document.getElementById("chip");
  var chipText = document.getElementById("chip-text");
  var title = document.getElementById("title");
  var lead = document.getElementById("lead");
  var note = document.getElementById("note");
  var retry = document.getElementById("retry");
  var count = document.getElementById("count");
  var attempts = 0;
  var busy = false;

  var COPY = {
    offline: {
      chip: "Waiting for a connection",
      title: "Plug’s <br />pulled",
      lead: "SpondBot can’t reach the server. Reconnect to see or change your events.",
      note: true,
    },
    server: {
      chip: "Server not responding",
      title: "Back <br />in a moment",
      lead: "You’re online, but the SpondBot server isn’t answering. It may be restarting. This page keeps trying.",
      note: false,
    },
    back: { chip: "Back online", title: "Reconnected", lead: "Loading SpondBot…", note: false },
  };

  function show(state) {
    var c = COPY[state];
    chip.dataset.state = state;
    chipText.textContent = c.chip;
    title.innerHTML = c.title;
    lead.textContent = c.lead;
    note.hidden = !c.note;
  }

  function leave() {
    // Opened directly at /offline.html there is nothing to reload: go to the app.
    if (location.pathname === "/offline.html") location.replace("/");
    else location.reload();
  }

  function check() {
    if (busy) return;
    busy = true;
    retry.disabled = true;
    chip.dataset.state = "checking";
    chipText.textContent = "Checking the connection…";
    attempts += 1;
    count.textContent = attempts > 1 ? "_" + attempts : "";

    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, 6000);
    fetch("/api/v1/health", { cache: "no-store", signal: controller.signal })
      .then(function (res) {
        if (res.ok) {
          show("back");
          setTimeout(leave, 400);
          return;
        }
        show("server");
      })
      .catch(function () { show("offline"); })
      .finally(function () {
        clearTimeout(timer);
        busy = false;
        retry.disabled = false;
      });
  }

  retry.addEventListener("click", check);
  window.addEventListener("online", check);
  window.addEventListener("offline", function () { show("offline"); });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") check();
  });
  setInterval(function () {
    if (document.visibilityState === "visible") check();
  }, 8000);
  if (navigator.onLine) check();
})();
