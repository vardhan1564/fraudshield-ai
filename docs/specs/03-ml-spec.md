# Spec 03 — Machine Learning

## 1. Models
| Name | Purpose | Algorithm | Output | Artifact |
|---|---|---|---|---|
| `txn_model` | General fraud patterns | XGBoost (LightGBM as alt) | `txn_score` | `ml/artifacts/txn_model.json` |
| `anomaly_model` | Unusual behaviour vs own baseline | Autoencoder (PyTorch); Isolation Forest baseline | `anomaly_score` | `anomaly_model.pt` |
| `graph_model` | Mule / ring accounts | GraphSAGE (PyG) | `graph_score` | `graph_model.pt` |
| `social_model` | Scam-induced payments | LightGBM | `social_eng_score` | `social_model.txt` |
| `text_model` | Scam SMS / links | DistilBERT fine-tuned | label + prob | `text_model/` |
| `meta_model` | Combine scores | Logistic regression (calibrated) | `risk_score` | `meta_model.pkl` |

All registered in MLflow with: params, metrics, feature list (`features.json`), training data hash, model card.

## 2. Feature list (feature store contract)
Names below are **exact**; code must use them.

| Group | Feature | Type | Source |
|---|---|---|---|
| Txn | `amount` | float | request |
| Txn | `amount_log` | float | derived |
| Txn | `amount_ratio_avg` | float | amount / user 30-day avg (Redis profile) |
| Txn | `hour`, `weekday`, `is_night` | int/bool | timestamp |
| Txn | `channel_upi`, `channel_card`, `channel_wallet` | bool | request |
| Txn | `merchant_category` | categorical | request |
| Velocity | `txn_count_5m`, `txn_count_1h`, `txn_count_24h` | int | Redis |
| Velocity | `amount_sum_1h`, `amount_sum_24h` | float | Redis |
| Velocity | `failed_count_1h` | int | Redis |
| Payee | `payee_is_new` | bool | Postgres/Redis set of user payees |
| Payee | `payee_account_age_days` | int | payees table |
| Payee | `payee_unique_senders_24h` | int | Redis set size |
| Payee | `payee_reputation`, `payee_report_count` | float/int | payees |
| Device | `device_is_new`, `device_rooted`, `ip_distance_km`, `is_vpn`, `sim_changed_7d` | mixed | request + devices |
| Behaviour | `typing_speed_z`, `session_len_s`, `login_to_pay_s` | float | SDK |
| Context | `ctx_unknown_call`, `ctx_screen_share`, `ctx_call_long` | bool | request.context |
| Coach | `coach_red_flags` | int | coach answers (0 before coach) |
| Graph | `graph_score`, `graph_degree`, `graph_pagerank`, `hops_to_fraud`, `community_fraud_ratio` | float | Redis graph cache |

Definitions (T1.3, `ml/features/`; names and dtypes in `schema.py` → `features.json`). The online feature service must match these:
- "Before" means a strictly earlier timestamp: the payment itself, rows in the same second and rows exactly one window back are not counted. Windows are per user (velocity) or per payee.
- `amount_ratio_avg`: amount ÷ mean of the user's successful payments in the previous 30 days; 1.0 with no history.
- `hour`, `weekday`, `is_night`: IST; weekday 0 = Monday; night = 23:00–04:59.
- Velocity counts include failed attempts; `amount_sum_*` sums their amounts.
- `payee_is_new`: no earlier *successful* payment from this user to this payee. `payee_report_count`: reports filed before the payment.
- `payee_reputation`: F7 formula; `confirmed_fraud_cases` and `graph_flag` are 0 until T5.5 / phase 5.
- `device_is_new`: device first seen < 24 h before the payment (or unknown). `sim_changed_7d`: SIM change within the 7 days up to the payment.
- `ip_distance_km`: haversine from the payment geo to the user's home geo.
- `ctx_screen_share`: `screen_share_active` or a remote-access app is open.
- `merchant_category`: categorical with the fixed list in `schema.py` (P2P + 10 merchant categories).
- `coach_red_flags` and the graph features are 0 until the Scam Coach (T5.4) and graph (T5.1) exist.

## 3. Datasets
| Dataset | Use | Location |
|---|---|---|
| IEEE-CIS Fraud Detection (Kaggle) | Train/benchmark `txn_model` | `ml/data/raw/ieee/` |
| ULB Credit Card Fraud (Kaggle) | Quick baseline | `ml/data/raw/ulb/` |
| PaySim (Kaggle) | Mobile-money patterns | `ml/data/raw/paysim/` |
| Elliptic (Kaggle) | Graph model pre-training | `ml/data/raw/elliptic/` |
| SMS Spam Collection + own Indian scam SMS (≥ 500 labelled) | Text model | `ml/data/raw/sms/` |
| **Simulator output** | UPI-style data with scenarios | `ml/data/simulated/` |

Download via Kaggle CLI (`kaggle competitions download -c ieee-fraud-detection`). Raw data is git-ignored.

## 4. Simulator spec (`ml/simulator/`)
- Users: 10,000 with age band, city tier, avg amount, typical hours, 1–3 devices.
- Normal behaviour: Poisson daily txns, log-normal amounts, repeat payees 70%.
- Injected scenarios (labels in `scenario` column):
| Scenario | Share | Signature |
|---|---|---|
| `digital_arrest` | 0.4% | On unknown call 20+ min, screen share, new payee, amount 10–50× avg |
| `fake_support_refund` | 0.3% | Screen-share/remote app, payee recently reported |
| `investment_task` | 0.3% | Multiple increasing payments to new payee over days |
| `account_takeover` | 0.2% | New device, SIM changed, night, behaviour z > 3 |
| `mule_ring` | 20 rings × 10–30 accounts | Fan-in then fast forward |
| `card_testing` | 0.2% | Many small failed + one large |
- Hard negatives: legit big payments (rent, fees), legit new payees, legit night txns.
- Output: `transactions.parquet`, `users.parquet`, `devices.parquet`, `edges.parquet`, `reports.parquet`.

Implementation notes (T1.2, `ml/simulator/`; column lists in `schema.py`):
- `--n` is the total number of transaction rows; shares above are shares of those rows and are hit exactly (the last incident of each scenario is sized to fit). `--users` overrides the 10,000 customers.
- Mule-ring rows are extra rows, not a share. Every ring is built to trip the F3 rule detectors: 1–3 collectors each paid by 22–35 distinct customers within 24 h, 85–97% forwarded within 1 h through two layers to a cash-out wallet, ≥ 3 accounts per shared phone, about half the accounts opened < 7 days before. Mules are rows in `users` (`is_mule`, `ring_id`) and payees with `payee_user_id` set.
- There is no payees file: `transactions` carries `payee_user_id` and `payee_account_age_days` at payment time, and `reports.parquet` has timestamps, so report counts can be computed without looking into the future. About 2% of reports are false reports against legit payees.
- Extra ground-truth columns on `transactions`: `is_fraud`, `hard_negative` (`big_legit`, `new_payee_legit`, `night_legit`, `call_legit`), `ring_id`, plus `status` (SUCCESS/FAILED) for `failed_count_1h`. Context and behaviour columns use the spec 04 request names. Ground-truth columns must never be model features.
- `edges.parquet` has the same columns as the `graph_edges` table (spec 05).

## 5. Training protocol
- Split by **time** (train first 70%, val next 15%, test last 15%) — never random split.
- Imbalance: `scale_pos_weight` (XGBoost) or class weights; SMOTE only inside CV folds.
- Tuning: Optuna, 30 trials, objective = PR-AUC on val.
- Calibrate meta-model (isotonic).
- Thresholds chosen on val to hit FPR ≤ 1% for HOLD, ≤ 5% for STEP_UP.

## 6. Metrics (report all on test split)
| Metric | Target |
|---|---|
| PR-AUC | > 0.80 (IEEE-CIS) |
| ROC-AUC | > 0.92 |
| Recall @ 1% FPR | > 70% |
| F1 at chosen threshold | report |
| Per-scenario recall (simulator) | each ≥ 80% |
| Fairness: FPR gap across city tier / age band | < 2 points |
| Inference latency (all models, 1 row) | < 30 ms |

## 7. Explainability
- SHAP TreeExplainer for tree models; KernelExplainer only offline.
- Store top 10 SHAP values per scored txn in `model_scores.shap_json`.
- Global: SHAP summary plot saved to MLflow per model version.

## 8. Retraining & drift
- `ml/pipelines/retrain.py`: pulls labelled cases + recent simulated data, retrains `txn_model` + `meta_model`, compares to champion, registers challenger.
- Promotion only if challenger PR-AUC ≥ champion − 0.005 and latency within budget.
- Evidently `DataDriftPreset` daily; output HTML to `ml/reports/drift/`.

## 9. Model card template (`ml/models/<name>/MODEL_CARD.md`)
Purpose · training data · features · metrics · thresholds · limitations · fairness results · version · date.
