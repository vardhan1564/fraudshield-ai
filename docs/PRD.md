# PRD — FraudShield AI

| Field | Value |
|---|---|
| Product | FraudShield AI — AI-enabled real-time fraud detection |
| Document | Product Requirements Document (PRD) |
| Version | 1.1 (no native app; sized for 1–2 people) |
| Date | 17 Oct 2026 |
| Owner | Ashutosh (project lead) |
| Team | 1–2 people |
| Status | Approved for build |
| Related | `docs/specs/*`, `docs/PROGRESS.md` |

---

## 1. Summary
FraudShield AI scores every digital payment (UPI, card, wallet) in under 100 ms, explains each
decision in plain language, detects mule-account networks with graph ML, and steps in during
social-engineering scams **before** the money leaves — using a Scam Coach, a trusted-contact
approval, and a shared payee reputation score.

## 2. Problem statement
1. **Authorised push-payment scams** (digital arrest, fake customer care, investment/task scams) trick the real account holder into paying. OTP and 2FA do not help because the victim approves the payment.
2. **Mule-account networks** move stolen money through dozens of accounts in minutes. Per-transaction models can't see the ring.
3. **Black-box blocking.** Rule engines and ML models block payments without saying why. Customers lose trust; analysts can't act quickly; regulators ask for reasons.
4. **Stale models.** Fraud patterns shift every few weeks. Most systems retrain rarely.
5. **Siloed data.** Banks can't pool raw customer data, so each one sees only part of a fraud ring.

## 3. Goals and non-goals
### Goals (MVP)
| ID | Goal | Measure |
|---|---|---|
| G1 | Real-time scoring | p95 latency < 100 ms for `/transactions/score` |
| G2 | Accurate detection | PR-AUC > 0.80 on IEEE-CIS test split; recall > 70% at 1% FPR |
| G3 | Explain every decision | 100% of decisions return ≥ 3 `top_reasons` |
| G4 | Stop scams before payment | Scam Coach triggers on ≥ 90% of simulated scam scenarios |
| G5 | Find mule rings | ≥ 85% of injected rings flagged in simulation |
| G6 | Close the loop | Analyst label → retraining set within 1 minute |

### Non-goals (this version)
- Connecting to real banks, NPCI, or real payment rails (everything is simulated).
- Production-grade KYC, card tokenisation, or PCI-DSS certification.
- A native mobile app (Android/iOS). The customer side is a phone-sized web app (PWA).
- Reading real phone call state. Call and screen-share signals are simulated in the demo.
- Multi-tenant SaaS billing.

## 4. Users and personas
| Persona | Who | Needs | Primary surface |
|---|---|---|---|
| **Priya, customer** | 58-year-old UPI user, first-time digital banking | Warnings she understands, a way to stop and verify, family backup | Customer web app (`/app`, works on phone browser) |
| **Rahul, fraud analyst** | Works the alert queue at a bank | Clear reasons, linked-account view, fast approve/confirm | Dashboard (`/dashboard`) |
| **Meera, risk manager** | Owns fraud KPIs and rules | Trends, rule tuning, model health, audit trail | Dashboard (`/dashboard`) |
| **Partner developer** | Fintech integrating scoring | One clean REST API, docs, test keys | REST API + Swagger |

## 5. User stories (with acceptance criteria)
Priority: **P0** = MVP must-have · **P1** = should-have · **P2** = stretch.

### Epic A — Real-time scoring
| ID | Story | Priority | Acceptance criteria |
|---|---|---|---|
| A1 | As a partner app, I send a transaction and get a decision | P0 | `POST /transactions/score` returns `risk_score` 0–1, `decision` ∈ {APPROVE, STEP_UP, HOLD}, `top_reasons`, `latency_ms`; p95 < 100 ms at 50 rps |
| A2 | As a system, I compute velocity features live | P0 | Redis counters for 5 min / 1 h / 24 h update on every scored txn; read in < 5 ms |
| A3 | As a risk manager, I set thresholds without redeploying | P1 | Thresholds read from DB/env; change via `PUT /rules` takes effect within 10 s |
| A4 | As a system, every decision is stored | P0 | Row in `transactions` + `model_scores` with model version and SHAP JSON |

### Epic B — Explainability
| ID | Story | Priority | Acceptance criteria |
|---|---|---|---|
| B1 | As an analyst, I see why a payment was flagged | P0 | `GET /transactions/{id}/explain` returns SHAP contributions + one plain sentence |
| B2 | As a customer, I see a warning in my language | P1 | Explanation available in English, Hindi, Marathi |
| B3 | As an analyst, I see a SHAP waterfall chart | P0 | Case Detail page renders top 10 contributions |

### Epic C — Social-engineering defence (unique)
| ID | Story | Priority | Acceptance criteria |
|---|---|---|---|
| C1 | **Scam Coach:** As a customer, when a payment looks risky, I'm asked 2–3 quick questions | P0 | Triggered for STEP_UP; answers sent to `POST /transactions/{id}/coach`; score updated; outcome APPROVE / HOLD |
| C2 | **Call & screen-share signal:** the app reports if I'm on a call with an unknown number or sharing my screen | P0 | `context.on_call`, `caller_in_contacts`, `screen_share_active` accepted and used as features |
| C3 | **Trusted contact:** a family member must approve high-risk payments above my limit | P1 | Push to contact; approve/reject via `/approvals/{id}`; auto-hold after 10 min without answer |
| C4 | **Cooling-off timer:** risky payments wait 10 minutes unless verified | P1 | Configurable timer; user can cancel any time |

### Epic D — Network / mule detection (unique)
| ID | Story | Priority | Acceptance criteria |
|---|---|---|---|
| D1 | As a system, I build a graph of accounts, devices, IPs, payees | P0 | Edge rows written for every txn by the worker; graph rebuilt every 5 min (NetworkX); never in the request path |
| D2 | As a system, I flag mule patterns | P0 | Fan-in > 20 senders in 24 h + forward > 80% in 1 h → mule flag; GNN score available as feature |
| D3 | As an analyst, I explore linked accounts | P0 | Graph Explorer shows 2-hop neighbourhood; suspected ring highlighted |

### Epic E — Crowd intelligence (unique)
| ID | Story | Priority | Acceptance criteria |
|---|---|---|---|
| E1 | As a customer, I report a scam UPI ID / number in one tap | P0 | `POST /reports` stores report; payee `report_count` +1 |
| E2 | As a customer, I'm warned when paying a reported payee | P0 | `reputation_score` used as feature and shown in warning |
| E3 | As a customer, I check a suspicious SMS/link/QR | P1 | `POST /scan/message` returns label (phishing, fake KYC, lottery, job/task, safe) + confidence |

### Epic F — Analyst & admin workflow
| ID | Story | Priority | Acceptance criteria |
|---|---|---|---|
| F1 | As an analyst, I see live alerts | P0 | WebSocket pushes HOLD alerts within 1 s |
| F2 | As an analyst, I confirm fraud or mark legit | P0 | `PATCH /cases/{id}`; writes label + audit log |
| F3 | As a risk manager, I see KPIs | P0 | Overview: txns today, fraud caught, ₹ saved, FPR, p95 latency, charts |
| F4 | As an admin, I manage rules | P1 | CRUD rules UI; JSON condition validated |
| F5 | As an admin, I see model versions and drift | P1 | MLflow versions listed; Evidently drift status per day |
| F6 | As an auditor, I export the audit log | P1 | CSV export |

### Epic G — Learning loop
| ID | Story | Priority | Acceptance criteria |
|---|---|---|---|
| G1s | Analyst labels feed retraining | P0 | Labels stored in `cases`; `python -m ml.pipelines.retrain` uses them |
| G2s | Drift triggers retraining | P1 | Drift report > threshold → retrain job queued |
| G3s | Federated learning demo across 3 simulated banks | P2 | Flower server + 3 clients; global model beats each local model on held-out set |

### Epic H — Platform
| ID | Story | Priority | Acceptance criteria |
|---|---|---|---|
| H1 | Auth with roles | P0 | JWT login; roles customer / analyst / admin; protected routes |
| H2 | One-command local setup | P0 | `docker compose up` starts Postgres, Redis, MLflow; API, worker and web start with one command each |
| H3 | CI | P1 | GitHub Actions runs lint + tests on every push |
| H4 | Monitoring | P1 | Prometheus metrics; Grafana dashboard for latency/throughput |

## 6. Functional requirements (system view)
1. Accept a transaction, enrich it with features, score with an ensemble, decide, explain, store, and respond in < 100 ms.
2. Publish every scored transaction to a Redis Stream for the worker (alerts, graph edges, LLM explanation).
3. Generate the plain-language explanation asynchronously when the LLM is slow; push it via WebSocket.
4. Maintain payee reputation from crowd reports and confirmed cases.
5. Provide role-based dashboards and a customer app demonstrating each unique feature.
6. Record every decision and human action in an append-only audit log.

## 7. Non-functional requirements
| Category | Requirement |
|---|---|
| Performance | p95 < 100 ms scoring; 500 txn/s on a laptop (simulated) |
| Availability (demo) | Graceful degradation: if the worker, graph cache or LLM is down, scoring still works with the remaining models |
| Security | TLS in deploy; bcrypt passwords; JWT 30 min + refresh; rate limiting; secrets in env |
| Privacy | Data minimisation (DPDP Act 2023 principles); masked PII in logs/UI; consent flag for device signals |
| Explainability | Reasons on every decision; model card per model |
| Fairness | Compare FPR across city tier and age band; report gap |
| Maintainability | Typed code, tests ≥ 70% coverage on backend services, lint clean |
| Portability | Runs on Windows (native PowerShell or WSL2), macOS, Linux with Docker |

## 8. Decision logic (product rules)
| Score | Decision | Action | User sees |
|---|---|---|---|
| < 0.30 | APPROVE | Pay | Nothing (or green tick) |
| 0.30 – 0.70 | STEP_UP | Scam Coach → OTP → trusted contact (in that order, by risk) | Amber warning + questions |
| > 0.70 | HOLD | Pause; alert analyst | Red warning + "Call my bank" |
| Any + payee reported ≥ 5 times | STEP_UP minimum | Force warning | "37 people reported this payee" |
| Rule hit (blacklist) | HOLD | Override model | Red warning |

## 9. Success metrics
| Metric | Target |
|---|---|
| PR-AUC (transaction model) | > 0.80 |
| Recall @ 1% FPR | > 70% |
| p95 scoring latency | < 100 ms |
| Scam scenarios caught (simulated) | ≥ 90% |
| Mule rings found (simulated) | ≥ 85% |
| Analyst "reason is clear" rating | > 80% |
| Demo runs end-to-end without manual fixes | 3 consecutive runs |

## 10. Scope by release
| Release | Contents |
|---|---|
| **MVP (Week 9)** | Epics A, B1/B3, C1–C2, D1–D2, E1–E2, F1–F3, G1s, H1–H2 |
| **v1.0 (Week 12)** | + B2, C3–C4, D3, E3, F4–F6, G2s, H3–H4 |
| **Stretch** | G3s federated learning, behavioural biometrics model, Neo4j graph backend, Hindi/Marathi voice warnings |

Weeks assume **2 people**. Working **solo**, plan on about 16 weeks, or cut lower-priority (P1/P2) items to stay at 12.

## 11. Assumptions
- No public UPI dataset exists → we build a simulator with injected scam, mule and takeover scenarios.
- Device signals (call state, screen share) are simulated with toggles in the customer app. A real bank app would read them from the phone OS with consent.
- Team of 1–2 people working about 15–20 hours a week each.
- LLM API access is available; if not, Ollama or template sentences are used.

## 12. Risks and mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| Simulated data too easy → inflated metrics | Misleading results | Also report on IEEE-CIS; add noise and hard negatives to simulator |
| LLM latency breaks 100 ms budget | Failed G1 | Async explanation; template fallback |
| Too much infrastructure for a small team | Setup pain, lost weeks | Only Postgres + Redis + MLflow; Redis Streams instead of Kafka; NetworkX instead of Neo4j |
| Solo builder runs out of time | Unfinished demo | Strict P0 first; the cut list in the implementation plan says what to drop |
| Scope creep from 10 features | Late delivery | P0/P1/P2 discipline; gates in plan |
| Team member unavailable | Delay | Every task has written steps and an acceptance test |

## 13. Open questions
- [ ] Which 3 cities to model for the India heat map and fairness check?
- [ ] Final LLM provider for the demo (Anthropic API vs local Ollama)?
- [ ] Hosting for final demo: Render, Railway, or AWS free tier?

## 14. Glossary
| Term | Meaning |
|---|---|
| APP fraud | Authorised push-payment fraud: the real user is tricked into paying |
| Mule account | Account used to receive and pass on stolen money |
| Step-up | Extra check before a payment (questions, OTP, approval) |
| SHAP | Method that shows how much each feature pushed a prediction up or down |
| PR-AUC | Area under precision–recall curve; the right metric for rare fraud |
| VPA | UPI ID, e.g. `name@okbank` |
| Drift | Change in data patterns that makes a model less accurate |
