# Progress Tracker

> Update this file at the end of every task.
> Note format: `YYYY-MM-DD · A/B · one-line result · next step`

## Current status
- **Phase:** P0 Setup
- **Next task:** T0.4 (B) · then T1.1 (A)
- **Team:** A = ______ · B = ______ (solo: same person)
- **Blockers:** none

## Gates
- [x] P0 gate: infra up + `/api/v1/health` 200
- [ ] P1 gate: simulator + feature tests pass
- [ ] **Gate A**: PR-AUC > 0.80 (IEEE-CIS test)
- [ ] **Gate B**: p95 < 100 ms @ 50 rps
- [ ] **Gate C**: digital-arrest demo end to end
- [ ] Final: 3 clean demo runs

## Tasks
### P0 Setup (Week 1)
- [x] T0.1 Repo scaffold · A
- [x] T0.2 Local infrastructure · A
- [x] T0.3 Backend skeleton · A
- [ ] T0.4 Basic CI · B (workflow written + passes locally; tick after first green run on GitHub)

### P1 Data (Weeks 1–2)
- [ ] T1.1 Dataset download + EDA · A (script + notebook done; waiting on Kaggle token → download → run notebook)
- [ ] T1.2 UPI simulator · A
- [ ] T1.3 Feature builders · A

### P2 Core ML (Weeks 3–5)
- [ ] T2.1 Transaction model baseline · A
- [ ] T2.2 Tuning + SHAP + model card · A
- [ ] T2.3 Anomaly model · A
- [ ] T2.4 Social-engineering model · A
- [ ] T2.5 Meta-model + thresholds · A
- [ ] T2.6 Evaluation report (Gate A) · A

### P3 Backend (Weeks 5–7)
- [ ] T3.1 DB models, migrations, seed · B
- [ ] T3.2 Auth & roles · B
- [ ] T3.3 Online feature service · A
- [ ] T3.4 Model runtime + scoring endpoint · A
- [ ] T3.5 Explanations · A
- [ ] T3.6 Worker: Redis Streams + jobs · A
- [ ] T3.7 Alerts, cases, WebSockets · B
- [ ] T3.8 Remaining business APIs · B
- [ ] T3.9 Load test (Gate B) · B

### P4 Front ends (Weeks 6–9)
- [ ] T4.1 Web scaffold, shared layer, login · B
- [ ] T4.2 Dashboard: Overview · B
- [ ] T4.3 Dashboard: Live Monitor + Alert Queue · B
- [ ] T4.4 Dashboard: Case Detail · B
- [ ] T4.5 Customer app: shell, Home, Pay, Risk Warning · B
- [ ] T4.6 Customer app: Report Fraud · B
- [ ] T4.7 Dashboard: admin pages (P1) · B

### P5 Unique features (Weeks 10–11)
- [ ] T5.1 Graph refresh + detectors + graph API · A
- [ ] T5.2 GNN mule model · A
- [ ] T5.3 Dashboard: Graph Explorer · B
- [ ] T5.4 Scam Coach end to end · A + B
- [ ] T5.5 Payee reputation live · A
- [ ] T5.6 Trusted contact · B
- [ ] T5.7 Message scanner · A + B
- [ ] T5.8 Retraining + drift · A
- [ ] T5.9 Federated learning (optional) · A

### P6 Hardening (Week 12)
- [ ] T6.1 Test coverage + web CI · B
- [ ] T6.2 Load, fairness, security review · A
- [ ] T6.3 Deploy · B
- [ ] T6.4 Demo kit + report · A + B
- [ ] T6.5 Monitoring (optional) · A

## Cut list used
<!-- Record anything cut to stay on schedule -->

## Decision log
| Date | Decision | Why |
|---|---|---|
| 2026-10-17 | Customer app is a React PWA, not Flutter | Team of 1–2, no Flutter experience; device signals simulated anyway |
| 2026-10-17 | Redis Streams instead of Kafka/Redpanda | One less service; Redis already needed |
| 2026-10-17 | NetworkX instead of Neo4j (Neo4j optional) | Graph fits in memory; no extra database |
| 2026-10-17 | Develop on native Windows (PowerShell), WSL2 optional | Lead's machine runs natively; Docker Desktop handles WSL2 itself |
| 2026-10-17 | One `.venv` at repo root; `ml/requirements.txt` includes `backend/requirements.txt` | Solo-friendly; models trained with the same versions they're served with |

## Notes
<!-- newest first -->
- 2026-10-17 · A · T1.1 part 1: scripts/download_data.sh (Git Bash on Windows; validates names, skips finished sets) + 01_eda_ieee.ipynb (float32 load for 8 GB RAM), executed OK on fake IEEE-shaped data; ml/requirements installed · next: kaggle.json + accept IEEE rules, download, run notebook, record numbers here
- 2026-10-17 · B · T0.4 workflow: python-lint → python-test with postgres/redis services and pip cache; service connectivity tests fail in CI instead of skipping; URLs switched to 127.0.0.1 (localhost cost ~2 s per connection on Windows) · next: create GitHub repo + push to verify
- 2026-10-17 · A · T0.3 done: app factory, pydantic-settings, JSON logs + request id, async engine (verified against docker postgres), error format, /api/v1/health, /metrics; 25 backend tests pass; spec 04 notes on error codes and /metrics location · P0 gate passed · next T0.4
- 2026-10-17 · A · T0.2 done: postgres:16, redis:7 (AOF), mlflow v3.17.1 all healthy via compose; MLflow 3.x needs MLFLOW_ALLOW_FILE_STORE=true for the ./mlruns file store · next T0.3
- 2026-10-17 · A · T0.1 done: planned repo layout, pyproject (ruff/mypy strict/pytest), pinned requirements, README; ruff + mypy clean, 14 smoke tests pass · next T0.2 infra
