/**
 * Join page: accept an invite link (/join#<token>).
 *
 * The token lives in the URL fragment, which browsers never send to the server,
 * and is removed from the address bar as soon as it is read. It only travels in
 * the body of POST /invites/check and /invites/accept.
 */
(function () {
  const $ = (id) => document.getElementById(id);
  const token = decodeURIComponent(location.hash.slice(1));
  if (location.hash) history.replaceState(null, "", location.pathname);

  const REASONS = {
    used: "This invite has already been used. If you created a login with it, sign in with that login. Otherwise ask your admin for a new invite.",
    expired: "This invite has expired. Ask your admin for a new one.",
    unknown: "This invite link is not valid. Check that you copied the whole link, or ask your admin for a new one.",
  };

  function showInvalid(reason) {
    $("join-loading").hidden = true;
    $("join-form").hidden = true;
    $("join-invalid-text").textContent = REASONS[reason] || REASONS.unknown;
    $("join-invalid").hidden = false;
  }

  function showError(message) {
    $("join-error").textContent = message;
    $("join-error").hidden = false;
    $("join-error").scrollIntoView({ block: "nearest" });
  }

  async function check() {
    if (!token) return showInvalid("unknown");
    let result;
    try {
      result = await apiJson("/invites/check", "POST", { token });
    } catch (err) {
      $("join-loading").textContent = err.message;
      return;
    }
    if (!result.valid) return showInvalid(result.reason);
    if (result.note) {
      $("join-display-name").value = result.note;
      $("join-intro").textContent = `Hi ${result.note}. Pick a username and password for SpondBot, then connect your Spond account.`;
    }
    $("join-loading").hidden = true;
    $("join-form").hidden = false;
    $("join-username").focus();
  }

  $("join-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("join-error").hidden = true;
    const username = $("join-username").value.trim();
    const password = $("join-password").value;
    const repeat = $("join-repeat").value;
    const spondLogin = $("join-spond-login").value.trim();
    const spondPassword = $("join-spond-password").value;
    const displayName = $("join-display-name").value.trim();

    if (!username || !password || !spondLogin || !spondPassword) return showError("Fill in your username, password and Spond login.");
    if (password.length < 8) return showError("Your password needs at least 8 characters.");
    if (password !== repeat) return showError("The passwords do not match.");

    const btn = $("join-submit");
    btn.disabled = true;
    btn.textContent = "Checking with Spond…";
    try {
      await apiJson("/invites/accept", "POST", {
        token,
        username,
        password,
        spond_login: spondLogin,
        spond_password: spondPassword,
        display_name: displayName || null,
      });
      window.location.href = "/dashboard";
    } catch (err) {
      if (err.status === 410) return showInvalid(/used/.test(err.message) ? "used" : "unknown");
      showError(err.status === 429 ? "Too many attempts. Wait a minute and try again." : err.message);
      btn.disabled = false;
      btn.textContent = "Create login";
    }
  });

  check();
})();
