# Spec 04 — API

- Base URL: `/api/v1` · JSON · UTF-8 · timestamps ISO-8601 with offset.
- Auth: `Authorization: Bearer <JWT>`; partner systems use `X-API-Key`.
- Roles: `customer`, `analyst`, `admin`, `partner`.
- Errors: `{"error": {"code": "STRING_CODE", "message": "human text", "details": {}}}`.
- Pagination: `?page=1&page_size=50` → `{"items": [...], "page", "page_size", "total"}`.
- Swagger: `/docs` · OpenAPI JSON: `/openapi.json`.

## 1. Endpoint list
| # | Method | Path | Role | Purpose | Priority |
|---|---|---|---|---|---|
| 1 | POST | `/auth/register` | public | Create customer account | P0 |
| 2 | POST | `/auth/login` | public | Get access + refresh token | P0 |
| 3 | POST | `/auth/refresh` | any | New access token | P0 |
| 4 | GET | `/auth/me` | any | Current user | P0 |
| 5 | POST | `/transactions/score` | partner, customer | Score in real time | P0 |
| 6 | POST | `/transactions/{id}/coach` | customer | Submit Scam Coach answers | P0 |
| 7 | GET | `/transactions` | analyst, admin | List with filters | P0 |
| 8 | GET | `/transactions/{id}` | analyst, admin, owner | Detail | P0 |
| 9 | GET | `/transactions/{id}/explain` | analyst, admin, owner | SHAP + sentence | P0 |
| 10 | POST | `/device/signals` | customer | Behaviour + context signals | P1 |
| 11 | GET | `/alerts` | analyst | Open alerts | P0 |
| 12 | WS | `/ws/alerts` | analyst | Live alerts + explanation.ready | P0 |
| 12b | WS | `/ws/customer` | customer | explanation.ready, approval.requested, approval.resolved for this user | P0 |
| 13 | GET | `/cases/{id}` | analyst | Case detail | P0 |
| 14 | PATCH | `/cases/{id}` | analyst | Assign / status / label / notes | P0 |
| 15 | GET | `/graph/entity/{id}?depth=2` | analyst | Neighbourhood for Graph Explorer | P0 |
| 16 | GET | `/graph/rings` | analyst | Suspected rings | P1 |
| 17 | POST | `/reports` | customer | Report scam payee/number | P0 |
| 18 | GET | `/payees/{vpa}/reputation` | any | Payee risk | P0 |
| 19 | POST | `/scan/message` | customer | Classify SMS/link/QR | P1 |
| 20 | POST | `/trusted-contacts` | customer | Add contact + limit | P1 |
| 21 | GET | `/approvals/pending` | customer | Approvals waiting for me | P1 |
| 22 | POST | `/approvals/{id}` | customer | Approve / reject | P1 |
| 23 | GET | `/rules` · POST · PUT `/rules/{id}` · DELETE | admin | Rules CRUD | P1 |
| 24 | GET | `/models` | admin | Model versions | P1 |
| 25 | POST | `/models/{version}/deploy` | admin | Promote model | P1 |
| 26 | GET | `/metrics/overview?from=&to=` | analyst, admin | KPIs + chart data | P0 |
| 27 | GET | `/audit-log?entity=&actor=` | admin | Audit entries (+ `?format=csv`) | P1 |
| 28 | GET | `/health` · `/metrics` | public | Liveness · Prometheus. Health is `/api/v1/health` → `{"status":"ok"}`; Prometheus is at root `/metrics` (outside the base URL, so `/api/v1/metrics/overview` can't clash and Nginx won't expose it publicly) | P0 |

## 2. Core contracts

### 2.1 `POST /transactions/score`
Request:
```json
{
  "txn_id": "TXN-20261016-00042",
  "user_id": "U1029",
  "amount": 48500.0,
  "currency": "INR",
  "channel": "UPI",
  "payee_vpa": "refund.help@okbank",
  "merchant_category": "P2P",
  "device_id": "D-88a1",
  "ip": "103.21.44.10",
  "geo": {"lat": 19.99, "lon": 73.79},
  "timestamp": "2026-10-16T02:14:05+05:30",
  "context": {
    "on_call": true, "caller_in_contacts": false, "call_duration_s": 1420,
    "screen_share_active": true, "remote_access_app": "AnyDesk"
  },
  "behaviour": {"typing_speed_z": 0.4, "session_len_s": 95, "login_to_pay_s": 30}
}
```
Rules: `amount` > 0 and ≤ 10,000,000; `currency` = INR; `channel` ∈ UPI|CARD|WALLET; `txn_id` unique (409 on duplicate).

Response 200:
```json
{
  "txn_id": "TXN-20261016-00042",
  "risk_score": 0.86,
  "decision": "STEP_UP",
  "action": "SCAM_COACH",
  "model_scores": {"transaction": 0.71, "anomaly": 0.64, "graph": 0.82, "social_eng": 0.93},
  "top_reasons": [
    {"feature": "payee_report_count", "value": 37, "impact": 0.21, "text": "Payee reported by 37 users"},
    {"feature": "ctx_unknown_call", "value": true, "impact": 0.18, "text": "You are on a call with an unknown number"},
    {"feature": "ctx_screen_share", "value": true, "impact": 0.12, "text": "Screen sharing is on"}
  ],
  "explanation": "This looks like a refund scam. Real banks never ask you to pay to receive a refund.",
  "explanation_status": "ready",
  "coach_questions": [
    {"key": "on_call_instructed", "text": "Is someone on a call telling you to make this payment?"},
    {"key": "refund_to_receive", "text": "Are you paying to receive a refund, prize or cashback?"}
  ],
  "model_version": "ensemble-2026.10.16-1",
  "degraded": [],
  "latency_ms": 64
}
```
`decision`: APPROVE | STEP_UP | HOLD. `action`: NONE | SCAM_COACH | OTP | TRUSTED_CONTACT | ANALYST_REVIEW. `explanation_status`: ready | pending (then WS `explanation.ready`).

### 2.2 `POST /transactions/{id}/coach`
```json
{"answers": {"on_call_instructed": true, "refund_to_receive": false}}
```
→ `{"txn_id", "risk_score", "decision", "action", "explanation"}`

### 2.3 `GET /transactions/{id}/explain`
→ `{"txn_id", "shap": [{"feature","value","impact","text"}...10], "explanation", "language"}`

_(T3.5)_ `{id}` is the client `txn_id` or the transaction UUID. Optional `?lang=en|hi|mr` (default: the stored sentence's language; another language gets the template sentence). `shap` is the top 10 by |impact| in both directions (negative = made the payment look safer); `impact` is meta-model log-odds. Customers get 404 for payments that are not theirs; partners can't call it. In `/transactions/score`, `top_reasons` and `explanation` are in the customer's language, and `explanation_status` is `pending` except for APPROVE.

### 2.4 `PATCH /cases/{id}`
```json
{"status": "closed", "label": "fraud", "notes": "Confirmed with customer by phone"}
```

_(T3.7)_ `{id}` is the case UUID; every alert gets a case when the worker creates it, and `alert.created` carries `case_id`. Fields: `status` (the alert's: open | assigned | closed), `label` (fraud | legit), `notes`, `assigned_to` (an analyst/admin id, `null` unassigns; assigning an open alert makes it `assigned`). `null` for status/label/notes means no change. Closing needs a label (400 `VALIDATION_ERROR`). A new label sets `resolved_by/at`. Each change writes one `audit_log` row (`case.update`, before/after of the changed fields only) and, for a new label, a `stream:cases.labeled` event `{case_id, alert_id, txn_pk, txn_id, label, previous_label, labeled_by, labeled_at, user_id, payee_vpa, amount}` in the same transaction (outbox if Redis is down); then `case.updated` on `/ws/alerts`. Response: the case detail.

`GET /alerts?status=&page=&page_size=` (≤ 200) → page of `{alert_id, case_id, txn_id, severity, status, assigned_to, risk_score, decision, amount, channel, top_reasons, created_at}`; default open + assigned, most severe first, then oldest. `GET /cases/{id}` → `{case_id, alert_id, severity, status, assigned_to, label, notes, resolved_by, resolved_at, created_at, transaction: {id, txn_id, amount, currency, channel, merchant_category, payee_vpa, risk_score, decision, action, model_version, ts, customer: {id, external_id, name, phone (masked)}}, top_reasons, explanation}`. Endpoints 11, 13, 14 are analyst-only, as in the table.
`status`: open | assigned | closed · `label`: fraud | legit | null. Side effects: audit log, `cases.labeled` event, reputation update if fraud.

### 2.5 `GET /graph/entity/{id}?depth=2`
```json
{"nodes": [{"id": "A1", "type": "Account", "label": "98xxxxxx21", "risk": 0.82, "flags": ["mule_suspect"]}],
 "edges": [{"source": "A1", "target": "P9", "type": "SENT", "amount": 12000, "ts": "..."}],
 "rings": [{"id": "R3", "members": ["A1", "A4"], "score": 0.91}]}
```

### 2.6 `POST /reports`
```json
{"payee_vpa": "refund.help@okbank", "phone": "+91XXXXXXXXXX", "scam_type": "fake_support_refund", "description": "Called as bank support", "evidence_url": null}
```

### 2.7 `POST /scan/message`
```json
{"text": "Your KYC is expiring, update at http://sbi-kyc-update.xyz", "language": "en"}
```
→ `{"label": "fake_kyc", "confidence": 0.94, "reasons": ["Lookalike bank domain", "Urgency words"], "advice": "Do not click. Banks never ask KYC via SMS links."}`

### 2.8 `GET /metrics/overview`
→ `{"txn_count", "fraud_caught", "amount_saved_inr", "false_positive_rate", "p95_latency_ms", "by_hour": [{"hour", "total", "flagged"}], "by_type": [{"scenario", "count"}], "by_city": [{"city", "flagged"}]}`

### 2.9 Auth (endpoints 1-4) _(added in T3.2)_
- `POST /auth/register` `{"name", "phone" (10-digit Indian mobile), "email", "password" (8-72 bytes), "language"?}` → **201** token pair. Creates a `customer` only (staff come from the seed/admin). Email is stored lower-case; taken → **409 `EMAIL_TAKEN`**.
- `POST /auth/login` `{"email", "password"}` → token pair. Wrong email and wrong password give the same 401. More than `LOGIN_RATE_LIMIT_PER_MINUTE` (10) attempts per client IP per minute → **429 `RATE_LIMITED`** with `Retry-After`.
- `POST /auth/refresh` `{"refresh_token"}` → new token pair. Each refresh token works once (rotation); reuse → 401.
- `GET /auth/me` → `{"id", "name", "phone" (masked `98xxxxxx21`), "email", "role", "language"}`.
- Token pair: `{"access_token", "refresh_token", "token_type": "bearer", "expires_in": 1800}`. JWT HS256 claims: `sub` (user id), `role`, `type` (`access`|`refresh`), `jti`, `iat`, `exp`. 401 responses carry `WWW-Authenticate: Bearer`.
- Partners: `X-API-Key` checked against `PARTNER_API_KEYS` (`name:key,...` in `.env`, constant-time compare); no user row. Routers use `require_role(...)` or, where partners are allowed, `require_principal(...)` from `app/api/deps.py`.

## 3. WebSockets
Both fed by Redis pub/sub (spec 02 §6) so they work with several API workers.

_(T3.7)_ Paths are under the base URL: `/api/v1/ws/alerts`, `/api/v1/ws/customer`. `?token=` is an access token; a bad token or wrong role (analyst for alerts, customer for customer) closes with **1008**. The server subscribes before accepting, forwards messages unchanged, ignores anything the client sends, and closes with 1008 when the token expires (reconnect with a fresh token).

### `/ws/alerts` (analyst)
Auth via `?token=`. Server messages:
```json
{"type": "alert.created", "data": {"alert_id", "txn_id", "risk_score", "decision", "top_reasons", "created_at"}}
{"type": "explanation.ready", "data": {"txn_id", "explanation"}}
{"type": "case.updated", "data": {"case_id", "status", "label"}}
```

### `/ws/customer` (customer)
Auth via `?token=`. Only messages for the logged-in user.
```json
{"type": "explanation.ready", "data": {"txn_id", "explanation"}}
{"type": "approval.requested", "data": {"approval_id", "txn_id", "requester_name", "amount", "payee_vpa", "top_reasons", "expires_at"}}
{"type": "approval.resolved", "data": {"approval_id", "txn_id", "status"}}
```

## 4. Error codes
| HTTP | code | When |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Bad body |
| 401 | `UNAUTHORIZED` | No/invalid token |
| 403 | `FORBIDDEN` | Wrong role |
| 404 | `NOT_FOUND` | Unknown id |
| 409 | `DUPLICATE_TXN` | Same txn_id scored twice |
| 405 | `METHOD_NOT_ALLOWED` | Path exists, wrong HTTP method |
| 429 | `RATE_LIMITED` | > 100 req/s per key |
| 500 | `INTERNAL_ERROR` | Unexpected server error (no internals in the message) |
| 503 | `MODEL_UNAVAILABLE` | Models not loaded |

Specific codes (`DUPLICATE_TXN`, `MODEL_UNAVAILABLE`) are raised by services via `AppError`. Any other
409 or 503 falls back to the generic `CONFLICT` / `SERVICE_UNAVAILABLE`. Validation errors return
**400** (not FastAPI's default 422), with `details.errors = [{loc, msg, type}]` and never echo the input.
_(Note added in T0.3.)_
