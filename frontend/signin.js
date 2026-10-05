/**
 * SpondBot sign-in page: redirect when already signed in, show/hide password, submit.
 * Kept in its own file so the Content-Security-Policy can forbid inline scripts.
 */
(async function redirectIfSignedIn() {
  const user = await fetchCurrentUser();
  if (user) window.location.href = user.is_admin ? "/admin" : "/dashboard";
})();

document.getElementById("pw-toggle").addEventListener("click", (e) => {
  const input = document.getElementById("signin-password");
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  e.currentTarget.textContent = show ? "Hide" : "Show";
  e.currentTarget.setAttribute("aria-label", show ? "Hide password" : "Show password");
});

document.getElementById("signin-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = document.getElementById("signin-btn");
  const errEl = document.getElementById("signin-error");
  const username = document.getElementById("signin-username").value.trim();
  const password = document.getElementById("signin-password").value;
  errEl.hidden = true;
  if (!username || !password) {
    errEl.textContent = "Enter your username and password.";
    errEl.hidden = false;
    return;
  }
  btn.disabled = true;
  btn.textContent = "Signing in…";
  try {
    await apiJson("/auth/login", "POST", { username, password });
    const user = await fetchCurrentUser();
    window.location.href = user && user.is_admin ? "/admin" : "/dashboard";
  } catch (err) {
    errEl.textContent = err.status === 429 ? "Too many sign-in attempts. Wait a minute and try again." : err.message;
    errEl.hidden = false;
    btn.disabled = false;
    btn.textContent = "Sign in";
  }
});
