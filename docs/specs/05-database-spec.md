# Spec 05 — Data Stores

Migrations: Alembic in `backend/alembic/`. IDs: UUID v4 unless noted. All tables have `created_at timestamptz default now()`.

## 1. PostgreSQL tables
### users
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| external_id | text unique | e.g. `U1029` (simulator id) |
| name | text | |
| phone | text | stored full, masked in API |
| email | text unique null | |
| password_hash | text | bcrypt |
| role | enum(customer, analyst, admin, partner) | |
| language | enum(en, hi, mr) | default en |
| city, city_tier | text, smallint | fairness checks |
| age_band | text | `18-25`, … `60+` |
| kyc_level | smallint | |
| avg_txn_amount | numeric(14,2) | refreshed nightly |

### devices
id · user_id FK · fingerprint text · os text · is_rooted bool · first_seen · last_seen

### payees
id · vpa_or_account text unique · account_user_id FK users null (set when the payee is also a customer account, needed for mule pass-through) · display_name · account_age_days int · report_count int default 0 · confirmed_fraud_count int default 0 · reputation_score real default 0 · graph_flag bool default false

### transactions
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| txn_id | text unique | client id |
| user_id | uuid FK users | index |
| payee_id | uuid FK payees | index |
| device_id | uuid FK devices null | |
| amount | numeric(14,2) | |
| currency | char(3) | |
| channel | enum(UPI, CARD, WALLET) | |
| merchant_category | text | |
| ip | inet | |
| geo_lat, geo_lon | real | |
| context_json | jsonb | call/screen-share signals |
| ts | timestamptz | index |
| risk_score | real | |
| decision | enum(APPROVE, STEP_UP, HOLD) | index |
| action | text | |
| model_version | text | |
| scenario | text null | simulator ground truth (never shown in UI) |
| latency_ms | int | |

### model_scores
txn_id FK (PK) · transaction_score · anomaly_score · graph_score · social_eng_score · meta_score · shap_json jsonb · explanation text · explanation_lang

### coach_sessions
id · txn_id FK · questions jsonb · answers jsonb · red_flags int · outcome text · completed_at

### alerts
id · txn_id FK · severity enum(low, medium, high) · status enum(open, assigned, closed) · assigned_to FK users null

### cases
id · alert_id FK unique · label enum(fraud, legit) null · notes text · resolved_by FK users · resolved_at

### reports
id · reporter_id FK users · payee_id FK · phone text null · scam_type text · description text · evidence_url text null

### trusted_contacts
id · user_id FK · contact_user_id FK · approval_limit numeric(14,2) · active bool

### approvals
id · txn_id FK · contact_id FK trusted_contacts · status enum(pending, approved, rejected, expired) · expires_at · responded_at

### rules
id · name · condition_json jsonb · action enum(STEP_UP, HOLD) · priority int · is_active bool · updated_by FK

Rule condition format:
```json
{"all": [{"field": "amount", "op": ">", "value": 50000}, {"field": "payee_is_new", "op": "==", "value": true}]}
```

### model_versions
id · name · version · mlflow_run_id · metrics_json · is_active bool · deployed_at

### audit_log (append-only)
id bigserial · actor_id FK null · action text · entity text · entity_id text · before_json · after_json · ip inet · ts

### graph_edges
id bigserial · edge_type enum(SENT, USED_DEVICE, LOGGED_FROM) · src_id text · dst_id text · amount numeric(14,2) null · txn_id text null · ts timestamptz

Written by the worker's `graph_edges` consumer for every scored transaction.

### rings
id · member_ids text[] · score real · detector text (`fan_in`, `pass_through`, `shared_device`, `new_account_burst`, `gnn`, `community`) · first_seen · last_seen · status enum(suspected, confirmed, dismissed)

### outbox
id bigserial · stream text · payload jsonb · created_at · sent_at null — fallback when Redis is down; the worker replays unsent rows.

Indexes: transactions(user_id, ts desc), transactions(payee_id, ts desc), alerts(status, created_at), reports(payee_id), graph_edges(ts), graph_edges(src_id), graph_edges(dst_id).

## 2. Redis keys
| Key | Type | TTL | Content |
|---|---|---|---|
| `vel:{user_id}:5m` / `:1h` / `:24h` | hash {count, sum} | window | velocity |
| `fail:{user_id}:1h` | int | 1 h | failed attempts |
| `payees:{user_id}` | set | 90 d | known payees |
| `payee:{vpa}:senders:24h` | set | 24 h | distinct senders |
| `profile:{user_id}` | hash | 24 h | avg amount, usual hours, home geo |
| `graph:{account_id}` | hash | 10 min | graph_score, degree, pagerank, hops_to_fraud, community_ratio |
| `rep:{vpa}` | hash | 10 min | reputation_score, report_count |
| `rl:{api_key}` | int | 1 s | rate limit |

Use Redis pipelines; one round trip per request.

Redis Streams and pub/sub channels are listed in `docs/specs/02-architecture.md` §4 and §6.

## 3. Graph store (NetworkX, built from PostgreSQL)
The graph is not a separate database. The worker's `graph_refresh` job (every 5 min) loads `graph_edges` from the last 30 days into a NetworkX `MultiDiGraph`.

| Node type | Node id format | Source |
|---|---|---|
| Account | `A:{user_id}` | users |
| Payee | `P:{vpa}` (merged with its Account when `payees.account_user_id` is set) | payees |
| Device | `D:{fingerprint}` | devices |
| IP | `I:{ip}` | transactions.ip |

| Edge type | From → To | Attributes |
|---|---|---|
| `SENT` | Account → Payee | amount, ts, txn_id |
| `USED_DEVICE` | Account → Device | first_seen |
| `LOGGED_FROM` | Account → IP | ts |

Computed per account each refresh: degree, PageRank, hops to nearest confirmed-fraud node, Louvain community id and its fraud ratio, the four rule detectors (spec 01 F3), GNN score. Results go to Redis `graph:{account_id}` and suspected rings to the `rings` table.

**Optional Neo4j** (`GRAPH_BACKEND=neo4j`): same node/edge names as labels/types. Only add it if everything else is done.

## 4. Seed data
`scripts/seed.py` creates: 1 admin, 2 analysts, 20 customers with trusted contacts, 50 payees (5 reported), 5 rules. Credentials printed once; stored in `scripts/seed_credentials.local.txt` (git-ignored).
