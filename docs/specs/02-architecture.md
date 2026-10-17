# Spec 02 — System Architecture

Designed for a 1–2 person team: **two processes** (API + worker), **two data stores** (PostgreSQL + Redis), **one React project**.

## 1. Layers
| # | Layer | Components |
|---|---|---|
| 1 | Clients | Customer web app (`/app`, phone-sized PWA), analyst/admin dashboard (`/dashboard`), partner systems (REST) |
| 2 | API | FastAPI app: auth, REST routers, WebSockets (`/ws/alerts`, `/ws/customer`) |
| 3 | Real-time scoring | Feature builder → model ensemble → decision engine → SHAP reasons (inside the API process) |
| 4 | Worker | One Python process: Redis Stream consumers + APScheduler jobs (graph refresh, approval expiry, drift) |
| 5 | Data | PostgreSQL (records), Redis (features, caches, streams, pub/sub), MLflow (models), files (datasets) |
| 6 | Learning loop | Labels → retrain pipeline → MLflow → promote → API hot-reloads model |
| 7 | Observability (final phase) | Prometheus metrics, Grafana dashboard, JSON logs |

## 2. Modules (one backend package)
| Module | Path | Responsibility |
|---|---|---|
| API routers | `backend/app/api/v1/*.py` | HTTP + WS endpoints only; no business logic |
| Scoring | `services/scoring.py` | Orchestrates features → models → decision → reasons |
| Features | `services/features.py` | Builds the feature vector from request + Redis (one pipeline call) |
| Model runtime | `ml_runtime/registry.py` | Loads models from `ml/artifacts` at startup; hot reload on deploy |
| Decision engine | `services/decision.py` | Thresholds + rules + product rules → APPROVE / STEP_UP / HOLD + action |
| Explainer | `services/explain.py`, `services/reason_map.py`, `services/llm/*` | SHAP reasons; LLM sentence |
| Scam Coach | `services/scam_coach.py` | Question selection, answer scoring |
| Graph | `services/graph_service.py` | NetworkX graph build, detectors, PageRank, ring finding |
| Reputation | `services/reputation.py` | Payee score updates |
| Rules | `services/rules.py` | JSON-condition evaluation |
| Events | `streaming/bus.py` (publish), `streaming/consumers/*.py`, `streaming/worker.py` | Redis Streams |
| Jobs | `jobs/*.py` | Scheduled tasks run by the worker |
| Realtime push | `services/notify.py` | Redis pub/sub → API WebSocket fan-out |
| Auth | `core/security.py` | JWT, password hashing, role guards |

## 3. Scoring request path (synchronous, < 100 ms)
| Step | What | Budget |
|---|---|---|
| 1 | Validate request (Pydantic), auth | 3 ms |
| 2 | One Redis pipeline: velocity, profile, payee set, `graph:*`, `rep:*` | 5 ms |
| 3 | Build feature vector (numpy) | 3 ms |
| 4 | Run models (XGBoost, autoencoder, social-eng LightGBM) | 20 ms |
| 5 | Meta-model + rules + decision | 2 ms |
| 6 | SHAP top reasons + template sentence | 15 ms |
| 7 | Update Redis counters; `XADD` to `stream:transactions.scored`; Postgres write as background task | 10 ms |
| | **Total target** | **~60 ms**, 40 ms headroom |

The LLM sentence is never awaited in the request. The response carries the template sentence with `explanation_status: "pending"`; the worker's `llm_explainer` writes the LLM sentence and pushes `explanation.ready`.

## 4. Events (Redis Streams, consumer group `workers`)
| Stream | Published by | Consumers (in worker) |
|---|---|---|
| `stream:transactions.scored` | scoring service | `alert_pusher` (HOLD → alert row + WS), `llm_explainer`, `graph_edges` (append edge rows) |
| `stream:cases.labeled` | cases API | `reputation_updater`, `retrain_collector` |
| `stream:reports.created` | reports API | `reputation_updater` |

Failed messages are retried 3 times, then copied to `stream:dead`.

## 5. Scheduled jobs (APScheduler in worker)
| Job | Every | What |
|---|---|---|
| `graph_refresh` | 5 min | Load last 30 days of edges from Postgres → NetworkX → detectors, PageRank, Louvain, GNN scores → write `graph:{account_id}` in Redis + `rings` table |
| `approval_expiry` | 1 min | Expire approvals older than 10 min → HOLD |
| `profile_refresh` | 1 h | Recompute user averages into `profile:{user_id}` |
| `drift_report` | daily | Evidently report to `ml/reports/drift/` |

## 6. Realtime push
API process subscribes to Redis pub/sub channels `ws:analysts` and `ws:user:{user_id}`; worker and API publish there. WebSocket endpoints forward messages to connected clients. This keeps WebSockets working with more than one API worker.

## 7. Degradation rules
| Failure | Behaviour |
|---|---|
| Graph cache missing | `graph_score` = 0; add `"graph"` to `degraded` |
| Redis down | Velocity features = 0; decision floor STEP_UP for amount > ₹10,000; events written to Postgres `outbox` table |
| LLM down | Template sentence only |
| Worker down | Scoring still works; alerts appear when the worker restarts (streams keep messages) |
| Model load fails at boot | API refuses to start (fail fast) |

## 8. Deployment view
- **Local:** `infra/docker-compose.yml` runs postgres, redis, mlflow. API, worker and Vite run on the host for fast reload.
- **Demo:** one VM or Render: API container, worker container, Postgres, Redis, Nginx serving the built React app.

## 9. Architecture decisions (ADR log)
| ADR | Decision | Reason |
|---|---|---|
| 001 | One FastAPI app + one worker | 1–2 people; microservices add overhead |
| 002 | Redis Streams instead of Kafka | Redis is already needed; one less service; same consumer-group pattern |
| 003 | NetworkX instead of Neo4j by default | Graph fits in memory for simulated data; no extra database to run |
| 004 | Customer app is a React PWA, not Flutter | Same language as dashboard; no Dart/Android setup; device signals are simulated anyway |
| 005 | Ensemble + meta-model | Different fraud types need different models |
| 006 | Graph features precomputed and cached | Graph work is too slow for the request path |
| 007 | LLM sentence async | Protects the 100 ms budget |
| 008 | UPI simulator | No public UPI dataset |
