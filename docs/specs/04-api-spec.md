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
| 28 | GET | `/health` · `/metrics` | public | Liveness · Prometheus | P0 |

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

### 2.4 `PATCH /cases/{id}`
```json
{"status": "closed", "label": "fraud", "notes": "Confirmed with customer by phone"}
```
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

## 3. WebSockets
Both fed by Redis pub/sub (spec 02 §6) so they work with several API workers.

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
| 429 | `RATE_LIMITED` | > 100 req/s per key |
| 503 | `MODEL_UNAVAILABLE` | Models not loaded |
