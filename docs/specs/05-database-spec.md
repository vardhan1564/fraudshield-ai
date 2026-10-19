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
txn_id FK (PK) · transaction_score · anomaly_score · graph_score · social_eng_score · meta_score · shap_json jsonb (_T3.5_: `{"top": [{"feature","value","impact","rule"?}...10], "rule_hits": [...]}`) · explanation text · explanation_lang

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

Indexes: transactions(user_id, ts desc), transactions(payee_id, ts desc), alerts(status, created_at), reports(payee_id), graph_edges(ts), graph_edges(src_id), graph_edges(dst_id), graph_edges(txn_id) _(T3.6: the consumer replaces a transaction's rows on retry)_.

## 2. Redis keys
| Key | Type | TTL | Content |
|---|---|---|---|
| `vel:{user_id}` | sorted set | 30 d | the user's attempts: member `{txn_id}\|{amount}\|{status}`, score = epoch second (velocity, failed count, 30-day average) |
| `payees:{user_id}` | hash | 90 d | payee VPA → first successful payment (epoch s) |
| `payee:{vpa}:senders` | sorted set | 24 h | member `{user_id}\|{txn_id}`, score = epoch second (distinct senders) |
| `profile:{user_id}` | hash | none (refreshed by sync) | home_lat, home_lon, sim_changed_at (epoch s) |
| `device:{device_id}` | hash | none | first_seen (epoch s), is_rooted (0/1) |
| `graph:{account_id}` | hash | 10 min | graph_score, degree, pagerank, hops_to_fraud, community_ratio |
| `rep:{vpa}` | hash | 10 min | reputation_score, report_count, account_age_days |
| `rl:{api_key}` | int | 1 s | rate limit |
| `rl:login:{ip}` | int | 60 s | login attempts this minute (T3.2) |
| `refresh:{jti}` | string | refresh lifetime (7 d) | user id; deleted when the refresh token is used (rotation, T3.2) |

Use Redis pipelines; one round trip per request.

_Changed in T3.3:_ the velocity keys were fixed-TTL buckets (`vel:{user_id}:5m/:1h/:24h` hashes,
`fail:{user_id}:1h`, a `payees` set, a `senders:24h` set). Buckets only approximate a sliding window,
so online features could not equal the offline ones (T3.3 parity, tolerance 1e-6). Sorted sets of
timestamped events give exact open windows (`ZRANGEBYSCORE (t-w (t`), and the code lives in
`backend/app/services/features.py`.

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

## 5. Implementation notes (T3.1)
- Models: `backend/app/models/` (`core.py`, `operations.py`, `graph.py`); migration `backend/alembic/versions/*_initial_schema.py`.
- Nullability the table above leaves open: the customer profile columns of `users` (`city`, `city_tier`, `age_band`, `kyc_level`, `avg_txn_amount`) and `external_id` are null for staff; scoring columns of `transactions` are null until scored; `cases.resolved_by` is null until resolved.
- Added constraints: unique `(user_id, contact_user_id)` on `trusted_contacts`, unique `rules.name`, unique `(name, version)` on `model_versions`, partial index on unsent `outbox` rows, defaults `currency = 'INR'` and `rules.priority = 100`.
- `transactions(user_id, ts desc)` and `(payee_id, ts desc)` also serve as the `user_id` / `payee_id` indexes.
- `audit_log` is append-only via a trigger that rejects UPDATE and DELETE.
- Postgres enum type names: `user_role`, `user_language`, `txn_channel`, `txn_decision`, `rule_action`, `alert_severity`, `alert_status`, `case_label`, `approval_status`, `edge_type`, `ring_status`.
- Tests run against a separate `<db>_test` database, never the dev data.
