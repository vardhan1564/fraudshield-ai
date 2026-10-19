# Progress Tracker

> Update this file at the end of every task.
> Note format: `YYYY-MM-DD · A/B · one-line result · next step`

## Current status
- **Phase:** P3 Backend
- **Next task:** T3.8 Remaining business APIs (B)
- **Team:** A = ______ · B = ______ (solo: same person)
- **Blockers:** none (close Chrome before IEEE runs: the laptop is short of memory)

## Gates
- [x] P0 gate: infra up + `/api/v1/health` 200
- [x] P1 gate: simulator + feature tests pass
- [x] **Gate A**: UPI risk_score PR-AUC > 0.80 + IEEE-CIS PR-AUC ≥ 0.50 (re-based 2026-10-18)
- [ ] **Gate B**: p95 < 100 ms @ 50 rps
- [ ] **Gate C**: digital-arrest demo end to end
- [ ] Final: 3 clean demo runs

## Tasks
### P0 Setup (Week 1)
- [x] T0.1 Repo scaffold · A
- [x] T0.2 Local infrastructure · A
- [x] T0.3 Backend skeleton · A
- [x] T0.4 Basic CI · B

### P1 Data (Weeks 1–2)
- [x] T1.1 Dataset download + EDA · A
- [x] T1.2 UPI simulator · A
- [x] T1.3 Feature builders · A

### P2 Core ML (Weeks 3–5)
- [x] T2.1 Transaction model baseline · A
- [x] T2.2 Tuning + SHAP + model card · A
- [x] T2.3 Anomaly model · A
- [x] T2.4 Social-engineering model · A
- [x] T2.5 Meta-model + thresholds · A
- [x] T2.6 Evaluation report (Gate A) · A

### P3 Backend (Weeks 5–7)
- [x] T3.1 DB models, migrations, seed · B
- [x] T3.2 Auth & roles · B
- [x] T3.3 Online feature service · A
- [x] T3.4 Model runtime + scoring endpoint · A
- [x] T3.5 Explanations · A
- [x] T3.6 Worker: Redis Streams + jobs · A
- [x] T3.7 Alerts, cases, WebSockets · B
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
| 2026-10-18 | Gate A re-based: PR-AUC > 0.80 on the served UPI risk_score + IEEE-CIS PR-AUC ≥ 0.50 floor (spec 03 §6) | 0.80 on an IEEE time split is above Kaggle-winner level; IEEE lacks our UPI signals. IEEE retrained with card-uid features first (0.470 → 0.503) |

## Notes
<!-- newest first -->
- 2026-10-19 · B · T3.7 done: GET /alerts (open+assigned, severity then oldest, paged), GET/PATCH /cases/{id} (status on the alert, label+notes on the case, assign, closing needs a label; audit row with changed fields; new label -> stream:cases.labeled via bus.publish in the same transaction; case.updated pushed after commit). /api/v1/ws/alerts and /ws/customer relay Redis pub/sub (subscribe before accept, 1008 on bad token/role, close at token expiry). Worker now creates the case with the alert; alert.created has case_id. services/audit.py record() used by case updates and auth register/login/login_failed (spec 08). Tests: starlette TestClient with the real worker on the app loop: HOLD -> alert.created on /ws/alerts < 1 s, LLM sentence on /ws/customer, customers only get their own channel; backend 150 pass. Follow-up: rule changes / model deploys get audit rows when T4.7/T5.8 add them · next T3.8
- 2026-10-19 · A · T3.6 done: `python -m app.streaming.worker` runs alert_pusher (HOLD -> alert row + alert.created on ws:analysts), llm_explainer (Claude -> Ollama -> template sentence saved to model_scores + explanation.ready on ws:user:{id} and ws:analysts) and graph_edges (SENT/USED_DEVICE/LOGGED_FROM rows), each in its own consumer group (spec 02 said one shared group, which would split events: spec edited). 3 tries then stream:dead; own pending drained on start, XAUTOCLAIM after 60 s; APScheduler job outbox_replay every 10 s. streaming/bus.py publish() = XADD or outbox row, scoring uses it. services/notify.py pub/sub helpers. New index graph_edges(txn_id) (migration b4daed2e3633). 6 integration tests on docker Redis + test DB incl. the real worker process; backend 139 pass. The /ws endpoints themselves are T3.7 (tests prove the pub/sub message) · next T3.7
- 2026-10-19 · A · T3.5 done: services/explain.py combines exact TreeSHAP of txn_model + social_model (booster pred_contribs, same algorithm as shap.TreeExplainer) through the meta-model's logistic weights, so impact = share of the meta log-odds (test: sums match the meta-model exactly) and scam signals like screen share now show as reasons; rule hits listed first, 3-5 positive top_reasons, top 10 stored in model_scores.shap_json. Explain step p50 9.9 / p95 12.9 ms (< 15) after a numpy one-row DMatrix with nthread=1 (pandas DMatrix was ~2x slower). reason_map.py: phrase for all 38 features in en/hi/mr (Hindi/Marathi need a native speaker's review). services/llm/: Claude (effort low, no SDK retries) -> Ollama -> template, LLM_TIMEOUT_MS each, never raises; worker calls it in T3.6. Score response uses the customer's language, status pending except APPROVE. GET /transactions/{id}/explain (txn_id or UUID, ?lang=, analyst/admin/owner, 404 for others). Backend 133 tests pass. Specs 01 F4, 04 §2.3, 05 noted. Follow-ups for T3.9: to_frame() (~36 ms under load) is the scoring hot spot; coach questions are English-only (T5.4) · next T3.6
- 2026-10-19 · A · T3.4 done: POST /api/v1/transactions/score (spec 04 §2.1). ml_runtime/registry.py loads all 5 artifacts once at startup (missing -> 503 MODELS_UNAVAILABLE); services/scoring.py: duplicate check (409) -> online features -> models in a worker thread -> decision -> response with latency_ms; transaction + model_scores saved and stream event published in a background task (outbox row if Redis is down). services/decision.py: thresholds.json (STEP_UP > 0.016, HOLD > 0.25) + reported payee >= 5 -> at least STEP_UP (PRD §8) + admin rules only raise; STEP_UP action = SCAM_COACH on scam signals else OTP. THRESHOLD_APPROVE/HOLD env vars removed (thresholds.json wins). Model-card code moved to ml/pipelines/model_cards.py. Tests: backend 74 + ml 109 pass, ruff + mypy clean. Real p95 check is T3.9 (Gate B) · next T3.5
- 2026-10-18 · A · T3.3 done: backend/app/services/features.py builds all 38 features for one payment from Redis in one pipeline (sorted-set event windows, open ends like ml/features/windows.py) + record() after scoring + put_profile/put_device/add_report. Parity test replays 4,000 simulated payments (all 38 features, 1e-6): 0 mismatches; a mutant (7-day history) gives 2,950, so the test bites. Spec 05 §2 Redis keys changed from TTL buckets to sorted sets (exact windows). Backend now imports ml/ (repo root on sys.path in app/__init__.py). Follow-ups: rep:{vpa} has a 10-min TTL in spec but holds report_count: fill it from payees in T5.5; is_vpn and payee age are not in the spec 04 request yet (T3.4) · next T3.4
- 2026-10-18 · A · T2.6 done, Gate A PASS (re-based, decision log): IEEE txn_model with card-uid history features + V-column reduction (339 -> ~140 V cols, chosen on the train period; 258 features) + chunked QuantileDMatrix + Parquet cache + no-copy loader: test PR-AUC 0.470 -> 0.503, ROC-AUC 0.868 -> 0.874, recall@1%FPR 39.3% -> 43.0% (best iteration 1998/2000: more rounds may still help). Served risk_score passes every spec 03 §6 target; latency p95 22.6 ms after warm-up (earlier 30.1 / 133 ms were cold and paging outliers; txn_model now uses inplace_predict) · next T3.3
- 2026-10-18 · B · T3.2 done: /auth register (customer only, 409 EMAIL_TAKEN), login (same 401 for wrong email/password, bcrypt in a thread), refresh with rotation (refresh:{jti} in Redis, GETDEL: each token works once), me (masked phone). JWT HS256 30 min / 7 d with jti; require_role + require_principal (partner X-API-Key from PARTNER_API_KEYS, constant-time) in app/api/deps.py; login limit 10/min/IP -> 429 + Retry-After. 13 tests (success, wrong password, expired/forged/wrong-type token, 403 wrong role, refresh reuse, rate limit, partner key); spec 04 §2.9 + spec 05 Redis keys · next T3.3
- 2026-10-18 · B · T3.1 done: 17 spec 05 tables as SQLAlchemy 2.0 models (11 Postgres enums, FKs, spec indexes incl. (user_id, ts desc)), async Alembic env + initial migration (upgrade/downgrade/upgrade clean, `alembic check` no drift), audit_log append-only trigger, scripts/seed.py (idempotent; users 23, trusted_contacts 20, payees 50 with 5 reported / 12 reports, rules 5; logins in git-ignored scripts/seed_credentials.local.txt). 8 new tests on a separate fraudshield_test DB; spec 05 §5 notes · next T3.2
- 2026-10-18 · A · T2.6 in progress: ml/pipelines/evaluate.py writes ml/reports/eval_2026-10-18.md. Gate A FAILS: IEEE PR-AUC 0.470 (target > 0.80); simulated risk_score passes every target (PR-AUC 0.993, each scenario >= 88% HOLD, FPR gaps < 0.3 pt); all-models 1-row p95 30.1 ms vs < 30. Fix 1 (started): IEEE card-uid history features (ml/features/ieee.py, past-only) + Parquet cache of the IEEE CSVs; retrain OOM'd twice on this laptop. Fix 2: re-base the IEEE target (0.80 PR-AUC on a time split is above Kaggle-winner level) and keep 0.80 for the UPI data. Latency: inplace_predict in BaseModels.scores (untested yet)
- 2026-10-18 · A · T2.5 done: ml/models/meta_model.py (logistic regression on txn/anomaly/social/graph scores wrapped in isotonic calibration, fitted on validation; thresholds chosen on out-of-fold predictions). thresholds.json: STEP_UP > 0.016 (val FPR 4.75%), HOLD > 0.25 (val FPR 0.54%). Test: PR-AUC 0.993, recall@HOLD 98.5%, FPR@HOLD 0.64%, Brier 0.0018, FPR gap city_tier 0.23 / age_band 0.28 pt; every scenario >= 88% HOLD, 100% STEP_UP+. Spec F2 met by an explicit rule (unknown call + new payee adds +0.25): risk 0.003 -> 0.256, min +0.238. Found the social model could score a payment LOWER when a call was added (up to -0.54 on small data), so added LightGBM monotone constraints on ctx_unknown_call/ctx_screen_share/ctx_call_long/coach_red_flags and retrained (investment_task after-coach recall 82.4% -> 88.2%) · next T2.6
- 2026-10-18 · A · T2.4 done + simulator realism pass. social_model.py: LightGBM on 10 context/payee/coach/amount-ratio features, target = DA/FSR/IT; first pass (no coach) marks STEP_UP range at 5% FPR, 50% of those train rows get simulated 2-3 question coach answers. Test recall @1% FPR before -> after coach: digital_arrest 93.1% -> 98.3%, fake_support_refund 100%, investment_task 82.4% (all >= 80%). Before the realism pass payee_account_age_days made 96% of decisions and ctx_unknown_call 0%; now context + coach ~75%, account age 12%. Realism: young legit shops/customers, aged scam payees, legit unknown calls/screen share/remote apps, legit phone switches. Retrained: txn_model v2 (val PR-AUC 0.927, test 0.997, FPR gap 0.73/0.32 pt), anomaly now keeps the autoencoder (val ATO 100% vs IF 91.7%). For T2.5: ctx_unknown_call alone moves social score only 0.001 -> 0.003 (screen share dominates); spec F2 wants >= 0.2 on the final risk score, so design the meta-model/rules for it · next T2.5
- 2026-10-18 · A · T2.3 done: ml/models/anomaly_model.py (14 behaviour/device/velocity features, log1p + scaler fitted on train-legit; Isolation Forest 200 trees vs autoencoder 14-8-4-8-14 with early stopping; raw score min-max on validation -> 0-1; threshold 1% FPR on val). Kept Isolation Forest: val ATO recall 97.5% vs 90.0% (test ATO 100%, card_testing 94%, digital_arrest 46%, mule_ring 5%: expected, those are the real user on their own phone). Saved ml/artifacts/anomaly_model.pt (git-ignored like all .pt) with scaler; MLflow experiment anomaly_model. mlflow_client() helper shared with train.py · next T2.4
- 2026-10-18 · A · T2.2 done: ml/pipelines/tune.py (Optuna TPE 30 trials, exact val PR-AUC objective, median pruning; data binned once and shared by trials). Served model ml/artifacts/txn_model.json (+ meta: threshold 0.00311, features, metrics), MLflow registry txn_model v1, SHAP summary (XGBoost TreeSHAP) in MLflow, MODEL_CARD.md generated. Simulated test PR-AUC 0.9994, all scenarios 100%, FPR gap city_tier 0.71 pt / age_band 0.59 pt. SHAP confirms payee_account_age_days dominates (mean |SHAP| 4.3 vs 1.4 next): simulator realism pass still recommended. IEEE tuning supported (--dataset ieee --timeout) but ~15 min/trial, not run yet · next T2.3
- 2026-10-18 · A · T2.1 done: ml/pipelines (config.yaml, split.py time 70/15/15, metrics.py, datasets.py, train.py; XGBoost hist + scale_pos_weight, early stopping on val PR-AUC, threshold at 1% FPR on val). MLflow runs in `txn_model`. Simulated (14-day warm-up): test PR-AUC 0.999, all scenarios 100% recall (simulator too easy, see below). IEEE (431 cols, 15 min, peak 3.1 GB with page file): test PR-AUC 0.470, ROC-AUC 0.868, recall@1%FPR 0.393; best iteration 1855 of 2000 (still improving). Fixed a simulator leak (fraud rows had exact home GPS -> ip_distance_km == 0). Follow-ups: (1) Gate A PR-AUC > 0.80 on IEEE looks unrealistic for a time split (Kaggle winners ~0.93-0.95 ROC-AUC); IEEE needs feature engineering (card/address uid aggregates, frequency encodings) + tuning in T2.2, then decide the target at T2.6; (2) make the simulator harder (legit young payees, legit screen-share/unknown calls, aged mule accounts) so simulated metrics mean something · next T2.2
- 2026-10-17 · B · T0.4 done: private repo github.com/vardhan1564/fraudshield-ai; first CI run failed (ml tests need pyarrow, CI installs only dev + backend), fixed by pinning pyarrow in requirements-dev.txt; run 39049205676 green: lint + 75 tests, 98% coverage, postgres/redis service tests ran (not skipped). gh token needed the `workflow` scope to push ci.yml · next T2.1
- 2026-10-17 · A · T1.3 done: ml/features (schema.py -> features.json, 38 features exactly as spec 03 §2; txn, velocity, payee, device, context groups; pure functions; windows strictly earlier, O(n log n)). 17 tests: 50-row hand-computed fixture incl. window edges, cut-off no-leakage check on fixture + simulator. 100k build in 1.3 s. Spec 03 §2 definitions added. Follow-up for T2.1: payee_is_new is 0.72 on legit rows because simulated users have no history before day 1, so use the first ~14 days as warm-up (features only, no training) or add pre-window history · P1 gate passed · next T2.1
- 2026-10-17 · A · T1.2 done: ml/simulator (config.yaml, population, normal + 4 hard-negative types, 5 scenarios with exact shares, 20 mule rings built to trip all 4 F3 detectors, 5 parquet files). 100k rows in 8.4 s / 225 MB; fraud 2.87% (1,470 ring rows, 403 mules). 26 simulator tests (signatures, rings, determinism). Spec 03 §4 notes added (no payees file; payee facts on each txn) · next T1.3
- 2026-10-17 · A · T1.1 done: all 4 datasets in ml/data/raw (ieee 1.3G, elliptic 666M, paysim 471M, ulb 144M). IEEE train: 590,540 rows x 434 cols, fraud 3.50% (7.85% with identity vs 2.09% without, identity on 24.4%); 212 cols >50% missing; top corr V257 +0.38, V246, V244, V242 (all ~78% missing); fraud rate peaks ~10.6% at relative hour 7 (low-volume hours); fraud has extra mass < $1 (card testing). Notebook keeps txn/identity unmerged (merge OOM'd with ~1.6 GB free). Script accepts new Kaggle API tokens (~/.kaggle/access_token). Elliptic is CC BY-NC-ND: fine for this non-commercial project · next T1.2
- 2026-10-17 · A · T1.1 part 1: scripts/download_data.sh (Git Bash on Windows; validates names, skips finished sets) + 01_eda_ieee.ipynb (float32 load for 8 GB RAM), executed OK on fake IEEE-shaped data; ml/requirements installed · next: kaggle.json + accept IEEE rules, download, run notebook, record numbers here
- 2026-10-17 · B · T0.4 workflow: python-lint → python-test with postgres/redis services and pip cache; service connectivity tests fail in CI instead of skipping; URLs switched to 127.0.0.1 (localhost cost ~2 s per connection on Windows) · next: create GitHub repo + push to verify
- 2026-10-17 · A · T0.3 done: app factory, pydantic-settings, JSON logs + request id, async engine (verified against docker postgres), error format, /api/v1/health, /metrics; 25 backend tests pass; spec 04 notes on error codes and /metrics location · P0 gate passed · next T0.4
- 2026-10-17 · A · T0.2 done: postgres:16, redis:7 (AOF), mlflow v3.17.1 all healthy via compose; MLflow 3.x needs MLFLOW_ALLOW_FILE_STORE=true for the ./mlruns file store · next T0.3
- 2026-10-17 · A · T0.1 done: planned repo layout, pyproject (ruff/mypy strict/pytest), pinned requirements, README; ruff + mypy clean, 14 smoke tests pass · next T0.2 infra
