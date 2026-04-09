# SpondBot Frontend

A clean, dark-themed static web frontend for the SpondBot RSVP automation backend.

## File Layout

```
frontend/
├── index.html      # Login / Register page
├── dashboard.html  # User: My Events dashboard
├── admin.html      # Admin: full management dashboard
├── app.js          # Shared API client, utilities, icons
└── style.css       # Obsidian Mint design system stylesheet
```

## Serving

The frontend is just static files — serve them with any web server:

```bash
# Python (development)
python -m http.server 8080 --directory frontend/

# Or add a static-files route to the FastAPI app (production)
```

## Configuration

By default, the frontend calls the API at the **same origin** (i.e. the backend URL).
If your frontend is served from a different domain, set the API base URL once in the browser console:

```js
localStorage.setItem('sb_api_url', 'https://your-backend.example.com');
```

Then refresh — all API calls will use that base URL.

## Auth Flow

| Page | Required Role | How it works |
|---|---|---|
| `index.html` | None | Login → JWT stored in `sessionStorage` |
| `dashboard.html` | Any logged-in user | JWT decoded to check `linked_user_id` for data scoping |
| `admin.html` | `is_admin: true` | `requireAdmin()` redirects non-admins to dashboard |

**Security notes:**
- JWT is stored in `sessionStorage` (not `localStorage`) — cleared when the browser tab closes
- The backend API key is **never** sent to or stored in the browser
- All sensitive operations are gated server-side by JWT claims

## First Login

The backend auto-seeds an admin account on startup from env vars:
```
ADMIN_USERNAME=admin
ADMIN_PASSWORD=changeme_admin_password   # ← change this!
```

Go to `/admin.html` → it redirects to `index.html` if not logged in → sign in with admin credentials.
Once logged in, use the Admin Dashboard → Users → "Add User" to create regular user accounts.
