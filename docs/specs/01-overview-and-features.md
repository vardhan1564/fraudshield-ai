# Spec 01 — Unique Features (detailed behaviour)

Each feature: what it does, inputs, logic, outputs, where it lives in code, and how to test it.
The PRD (`docs/PRD.md`) holds the why; this file holds the how.

---

## F1. Scam Coach (in-the-moment intervention) — P0
**Problem:** victims of digital-arrest / fake-support scams approve payments themselves.

**Trigger:** decision = `STEP_UP` and (`social_eng_score` ≥ 0.5 OR payee reported OR on call with unknown number).

**Flow**
1. Score API returns `action: "SCAM_COACH"` and `coach_questions` (2–3 items).
2. App shows the questions full-screen; payment button disabled.
3. App posts answers to `POST /transactions/{id}/coach`.
4. Backend recomputes `social_eng_score` with answers as features → new decision.
5. If any "red flag" answer is YES → HOLD + show tailored warning + "Call my bank" button.

**Question bank (pick by scam type predicted)**
| Key | Question | Red flag answer |
|---|---|---|
| `on_call_instructed` | Is someone on a call telling you to make this payment? | YES |
| `threat_or_arrest` | Did anyone say you'll be arrested, fined or your account blocked? | YES |
| `refund_to_receive` | Are you paying to receive a refund, prize or cashback? | YES |
| `promised_returns` | Were you promised high or guaranteed returns? | YES |
| `asked_secrecy` | Were you told not to tell family or the bank? | YES |
| `know_payee` | Have you met or paid this person before? | NO |

**Code:** `backend/app/services/scam_coach.py`, `frontend-web/src/customer/pages/ScamCoach.tsx`.
**Test:** simulator "digital_arrest" scenario → STEP_UP → answers with 1 red flag → HOLD.

---

## F2. Active-call & screen-share signal — P0
**Input fields** in `context`: `on_call` (bool), `caller_in_contacts` (bool), `call_duration_s` (int), `screen_share_active` (bool), `remote_access_app` (string|null).
**Features:** `ctx_unknown_call = on_call and not caller_in_contacts`, `ctx_screen_share`, `ctx_call_long = call_duration_s > 300`.
**Demo:** toggles in the customer app's **Simulate context** panel (spec 07). Browsers can't read call state, so this signal is simulated; a real bank app would read it from the phone OS with the user's consent.
**Test:** same transaction with and without `ctx_unknown_call` → score difference ≥ 0.2.

---

## F3. Mule-network graph detection — P0
**Graph (NetworkX, rebuilt every 5 min from PostgreSQL `graph_edges`; see spec 05 §3):** nodes Account, Payee, Device, IP; edges SENT, USED_DEVICE, LOGGED_FROM.
**Rule-based detectors (run in the `graph_refresh` job every 5 min):**
- Fan-in: payee receives from > 20 distinct senders in 24 h.
- Pass-through: > 80% of incoming amount forwarded within 1 h.
- Shared device: ≥ 3 accounts on one device.
- New-account burst: account < 7 days old with > 10 incoming.
**ML detector:** GraphSAGE node classifier (PyTorch Geometric) trained on simulated + Elliptic data → `graph_score` per account, cached in Redis `graph:{account_id}` (TTL 10 min).
**Community detection:** Louvain on SENT edges; community fraud ratio as feature.
**Code:** `ml/models/gnn.py`, `backend/app/services/graph_service.py`, `backend/app/jobs/graph_refresh.py`.
**Test:** simulator injects 20 rings → ≥ 17 flagged.

---

## F4. Explainable decisions in plain language — P0
- SHAP TreeExplainer on the transaction model (computed in-request; < 15 ms for 1 row).
- Map feature names to human phrases via `backend/app/services/reason_map.py`, e.g. `amount_ratio_avg` → "Amount is {x}× your usual".
- Top 3–5 positive contributions → `top_reasons`.
- LLM turns reasons into one sentence; prompt template in `backend/app/services/llm/prompts.py`; timeout 1.5 s; fallback template "Flagged because: {r1}, {r2}, {r3}."
- Languages: `en`, `hi`, `mr` (from user profile).

---

## F5. Behavioural biometrics — P1/P2
- SDK sends per session: avg keystroke interval, swipe velocity, tap pressure variance, time login→pay.
- Per-user baseline (rolling mean/std); autoencoder reconstruction error → `behaviour_score`.
- MVP: simulated values; feature exists in pipeline.

---

## F6. Trusted contact ("Second Pair of Eyes") — P1
- User sets contact + `approval_limit` (₹).
- If decision ≠ APPROVE and amount ≥ limit → create `approvals` row, notify the contact over `/ws/customer` (message `approval.requested`).
- Contact approves/rejects within 10 min; no answer → HOLD.

---

## F7. Payee reputation from crowd reports — P0
`reputation_score = min(1, 0.1*report_count + 0.5*confirmed_fraud_cases + 0.3*graph_flag)`
- Updated on every report and every confirmed case.
- Used as feature `payee_reputation` and as hard rule (report_count ≥ 5 → minimum STEP_UP).
- Public lookup: `GET /payees/{vpa}/reputation`.

---

## F8. Scam message / link / QR scanner — P1
- Input: text (SMS/WhatsApp), URL, or decoded QR payload.
- Classifier: DistilBERT fine-tuned (labels: `phishing`, `fake_kyc`, `lottery_prize`, `job_task`, `investment`, `safe`).
- URL checks: domain age (stub), lookalike of bank domains (Levenshtein), shortener, non-HTTPS.
- Output: label, confidence, reasons, advice sentence.

---

## F9. Federated learning across banks — P2
- Flower server + 3 clients, each with a different slice of simulated data.
- Model: same XGBoost-compatible MLP (or logistic) for FedAvg.
- Report: local-only vs federated PR-AUC per bank.

---

## F10. Self-learning loop with drift alerts — P1
- Analyst labels → `cases.label`.
- Daily Evidently report on feature distributions vs training reference.
- Drift share > 0.3 or PR-AUC on recent labels drops > 5 points → retrain job → new MLflow version → manual promote via `/models/{version}/deploy`.
