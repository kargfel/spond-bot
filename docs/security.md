# Security notes

What protects SpondBot, what the operator must set, and what is deliberately left open.
The assets worth protecting: members' **Spond credentials** (stored encrypted; whoever holds them can act as the member in Spond), the **admin login** (full control) and the integrity of the **answers** the bot sends.

## What is in place

| Area | Protection |
|---|---|
| Passwords | bcrypt, 72-byte limit handled by bytes (umlauts, emoji safe); login runs bcrypt even for unknown users (no timing leak) |
| Sessions | HttpOnly, `SameSite=Strict`, `Secure` on HTTPS, 8 h. **Checked against the database on every request**: a demoted admin, a deleted login or a changed password takes effect immediately (the cookie carries a fingerprint of the password hash). Tokens must carry an expiry |
| CSRF | `SameSite=Strict` plus JSON-only bodies (form and `text/plain` posts are rejected with 422) |
| Brute force | Per-IP limits: login 5/min, password change 5/min, Spond password 5/min, invite signup 5/min, invite check 20/min. Only as good as the IP, see `TRUSTED_PROXIES` below |
| Access control | Every route checks the session; members only reach their own events and Spond account; admin-only routes use `AdminDep`; `/docs`, `/redoc` and `/openapi.json` need an admin session; the last admin cannot be deleted or demoted, nobody can delete themselves |
| Secrets at rest | Spond passwords and tokens Fernet-encrypted; invite tokens stored only as SHA-256; backups are mode 0600 |
| XSS | All user-controlled text goes through `esc()` or `textContent`; regression tests plant markup in every view. The Content-Security-Policy forbids inline scripts, `eval` and framing, and allows scripts only from this site plus jsDelivr (Chart.js, pinned with an SRI hash) |
| Headers | CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, COOP/CORP, HSTS on HTTPS, `Cache-Control: no-store` on `/api/`, HTML always revalidated |
| SSRF | Push subscription endpoints must belong to FCM, Mozilla, Apple or Windows push services; Spond URLs are fixed |
| Input size | Request bodies over 1 MiB are refused (413) |
| Audit | Sign-ins (also failed), every change, refusals and the bot's actions are recorded with IP, browser and request ID; secrets are scrubbed; CSV export is formula-safe |
| Supply chain | `requirements.txt` is a lock, Dependabot weekly, CodeQL on every PR, `pip-audit` and `npm audit` clean at the time of writing |
| Container | Non-root user, `no-new-privileges`, all capabilities dropped, DB not published to the host, first admin refuses an example password |

## What the operator must do

1. **Set `TRUSTED_PROXIES`** to your reverse proxy's IP or network. Unset, only 127.0.0.1 is believed and every visitor looks like the proxy (one IP in the audit log, one shared login limit); `*` lets a visitor fake their IP and dodge the per-IP login limit. `scripts/proxy_check.py` finds the address and tests it before a deploy; afterwards **Audit** must show your own address. See `docs/setup.md`.
2. **Firewall port 8080** to the reverse proxy only (see `DEPLOY.md`). Docker-published ports bypass `ufw`: check from another machine that 8080 really is closed (`docs/setup.md`).
3. **Keep `FERNET_KEY` apart from the backups.** Backups plus key together expose every member's Spond credentials.
4. Use a **strong admin password** and tell members that sign-ins and changes are logged (IP addresses are personal data; `AUDIT_RETENTION_DAYS`).
5. Run behind **HTTPS** only (`SITE_DOMAIN` set to the real domain, so cookies are `Secure` and HSTS is sent).

## Known limits and open ideas

- **Logout does not revoke a stolen cookie** before it expires (8 h). Changing the password does. A server-side session list would fix it.
- **No per-account login throttling.** Limits are per IP; a distributed attack on one username is not slowed. A per-username limit would also let an attacker lock a victim's sign-in for a while, which is why it is a decision, not a default.
- **No second factor** for the admin login (TOTP would be the next step).
- The session signing key is the Fernet key itself. Deriving a separate key would be cleaner; it would sign everyone out once.
- Usernames are free text: look-alikes (`admin` vs `аdmin`) are possible when members choose their own at signup.
- `API_KEY` is required by the config but no route uses it any more.
- Event streams (SSE) have no per-user connection cap.
- The session cookie is not `__Host-` prefixed.
