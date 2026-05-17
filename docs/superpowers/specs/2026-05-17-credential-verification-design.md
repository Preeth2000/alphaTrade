# Credential Verification API — Design

**Date:** 2026-05-17  
**Status:** Approved

## Summary

Add a `POST /verify/{credential_type}` set of endpoints so the frontend can validate credentials before saving them. Verification only — no side effects, no persistence.

---

## Architecture

New router: `alphaTrade/api/routers/verify.py`  
Mounted at `/verify` in `app.py`.

All endpoints return structured JSON with `"valid": true/false`. Errors are returned as `200` with `"valid": false` and an `"error"` field — not raised as HTTP exceptions (except for auth failures on protected endpoints).

| Endpoint | Auth | Body |
|---|---|---|
| `POST /verify/t212` | alphaTrade key required | `account`, `api_key`, `secret_key` |
| `POST /verify/polygon` | alphaTrade key required | `api_key` |
| `POST /verify/alphatrade-key` | alphaTrade key required | none |

---

## Endpoints

### POST /verify/t212

**Request:**
```json
{
  "account": "demo" | "invest" | "isa",
  "api_key": "string",
  "secret_key": "string"
}
```

**Logic:**
- Map `account` to T212 base URL: `demo` → demo URL, `invest`/`isa` → live URL
- Instantiate `T212Client` with provided credentials and mapped env
- Call `GET /account/info`
- If 200: return valid + account info details
- If auth error or network error: return invalid + error string

**Limitation:** T212 v0 API does not return account type (invest vs ISA) in `/account/info`. Verification confirms credentials authenticate against the correct base URL only — invest vs ISA cannot be distinguished from the API response.

**Success response:**
```json
{
  "valid": true,
  "account": "invest",
  "details": {"id": 123456, "currencyCode": "GBP"}
}
```

**Failure response:**
```json
{
  "valid": false,
  "account": "invest",
  "error": "401 Unauthorized"
}
```

---

### POST /verify/polygon

**Request:**
```json
{
  "api_key": "string"
}
```

**Logic:**
- Call `GET https://api.polygon.io/v1/meta/exchanges?apiKey=...` (lightweight, no data quota)
- If 200: valid. Extract plan info from response headers and body.
- If error: return invalid + error string

**Plan info:** Best-effort from `X-RateLimit-Limit` response header and exchange count from body. Polygon has no explicit subscription endpoint.

**Success response:**
```json
{
  "valid": true,
  "details": {
    "rate_limit": "5",
    "exchanges_count": 42
  }
}
```

**Failure response:**
```json
{
  "valid": false,
  "error": "403 Forbidden"
}
```

---

### POST /verify/alphatrade-key

**No request body.**

Protected by standard auth middleware. Frontend sends candidate key in `X-API-Key` header. Middleware validates before handler runs — handler only reached if key is valid (or no key is stored).

- Key valid (or no key stored): `200 {"valid": true}`
- Key invalid: `403 {"detail": "Invalid API key"}` (raised by middleware, not handler)

---

## Error Handling

- Network/timeout errors on T212 or Polygon calls: caught, returned as `{"valid": false, "error": "<message>"}` — never propagated as 500
- T212 retries from `T212Client` are **disabled** for verify calls — use single-attempt `httpx` call to avoid slow UX on bad credentials
- Polygon call has 10s timeout

---

## What Is Not Included

- No saving of credentials on verify
- No side effects of any kind
- No brute-force rate limiting (local API, low risk)
