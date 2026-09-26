# Spond API Reference

> Derived from `app/core/spond_client.py`. Update this file whenever the client changes.

Base URL: `https://api.spond.com/core/v1/`

---

## Required Headers

Every authenticated request includes these headers (set by `_headers(token)` in `spond_client.py`):

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <token>` — raw Base64 string, do NOT decode |
| `content-type` | `application/json` |
| `user-agent` | `Spond-iOS/2.7.10 (2233; iPhone; iOS 26.2.1; Scale/3.00)` |
| `Accept-Encoding` | `deflate, gzip` |
| `accept-language` | `en` |
| `priority` | `u=3, i` |

The `user-agent` must mimic a mobile Spond client — requests without a recognised mobile UA may be rejected with unexpected 4xx errors. Update the version string if requests start failing after a Spond app release.

---

## Endpoints

### Login

**Method & Path:** `POST /core/v1/auth2/login`

**No `Authorization` header required.**

**Request body (email login):**
```json
{"email": "user@example.com", "password": "secret"}
```

**Request body (phone login):**
```json
{"phoneNumber": "+4712345678", "password": "secret"}
```

**Response (current):**
```json
{
  "accessToken": {
    "token": "<raw-base64-string>"
  }
}
```

**Token handling:** pass the `accessToken.token` value **as-is** in `Authorization: Bearer`. Do NOT base64-decode it — the decoded bytes are not a valid bearer token and Spond will return 401.

**Backwards compatibility:** the old response format returned `"loginToken": "<string>"` at the top level. The client checks `loginToken` first, then falls back to `accessToken.token`.

**Error:** any non-2xx response or a response with no recognisable token field raises `SpondAuthError`.

---

### Get Profile ID

**Method & Path:** `GET /core/v1/profile`

**Response (relevant field):**
```json
{"id": "<32-char-hex-profile-id>", ...}
```

The `id` field is the user's global Spond profile ID — stored in `users.profile_id`. Used as a fallback RSVP recipient for direct invites where no group member ID can be resolved.

---

### Get Upcoming Events

**Method & Path:** `GET /core/v1/sponds/upcoming`

**Query parameters:**

| Parameter | Value | Description |
|-----------|-------|-------------|
| `includeDeclined` | `true` | Include events the user previously declined |
| `minEndTimestamp` | `"2024-11-07T15:00:00.000Z"` | Only return events ending after this time |

**Response:** JSON array of event stub objects, each with at minimum `id` and `heading`.

---

### Get Bulk Event Details

**Method & Path:** `GET /core/v1/sponds/getBulk`

**Query parameter:** `ids=<comma-separated-event-ids>`

**Response:** JSON array of full event objects. Relevant fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Spond event ID |
| `heading` | string | Event title |
| `startTimestamp` | string | ISO-8601 UTC start time |
| `inviteTime` | string | ISO-8601 UTC time when RSVP window opens (sniper target) |
| `rsvpDate` | string | ISO-8601 UTC RSVP deadline |
| `recipients.group.id` | string | Group ID (used to resolve per-group member ID) |

**Chunking:** the endpoint has an undocumented ID limit. The client sends at most 50 IDs per request.

---

### Get Groups

**Method & Path:** `GET /core/v1/groups`

Returns all groups the authenticated user belongs to. Used by `resolve_recipient_id()` to find the per-group member ID required for RSVPs.

**Response:** JSON array of group objects. Relevant structure per group:

```json
{
  "id": "<group-id>",
  "members": [
    {
      "id": "<member-id>",
      "email": "...",
      "phoneNumber": "...",
      "profile": {
        "id": "<profile-id>",
        "email": "...",
        "phoneNumber": "..."
      }
    }
  ]
}
```

**Member resolution order:**
1. Match `member.profile.id == user.profile_id` (primary — always present, unambiguous)
2. Match `member.email` or `member.profile.email` or `member.phoneNumber` or `member.profile.phoneNumber` against the user's login (secondary — Spond sometimes omits `profile.id`)
3. Fall back to global `profile_id` if no match (used for direct invites or lookup failure)

---

### Submit RSVP

**Method & Path:** `PUT /core/v1/sponds/{spondEventId}/responses/{recipientId}`

`recipientId` is the **per-group member ID** from the groups endpoint, not the global profile ID.

**Request body:**
```json
{"accepted": true}
```
or
```json
{"accepted": false}
```

**Expected response:** `200` or `204` (no body required).

**Errors:**
- `401` → `SpondAuthError` (caller forces token refresh and retries once)
- Other non-2xx → `SpondAPIError`

---

## Timestamp Format

All timestamps from Spond follow ISO-8601 UTC with millisecond precision:
```
2024-11-07T15:00:00.000Z
```

Parsed by `_parse_dt()` in `spond_client.py` into UTC-aware Python `datetime` objects.

---

## Known Breaking Changes

| Date | Change | Impact |
|------|--------|--------|
| 2026-05-21 | Login endpoint moved from `/core/v1/login` to `/core/v1/auth2/login` | Bot silently fails to authenticate |
| 2026-05-21 | Login response changed from `loginToken` string to `accessToken.token` nested object | Token is null, all RSVPs fail |
