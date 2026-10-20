# Spec 08 — Security, Testing, CI/CD, Deploy, Demo

## 1. Security checklist
- [ ] Passwords bcrypt (cost 12); JWT HS256 30 min + refresh 7 d; refresh rotation.
- [ ] Role guard dependency on every router (`require_role("analyst")`).
- [ ] Rate limiting (slowapi or Redis counter) 100 req/s per key, 10/min on login.
- [ ] CORS limited to `CORS_ORIGINS`.
- [ ] Input validation via Pydantic; max body size 64 KB.
- [ ] PII masking helper used in all logs and API responses for non-owners.
- [ ] Secrets only in `.env`; `.env` git-ignored; `.env.example` kept current.
- [ ] Audit log for every case update, rule change, model deploy, login.
- [ ] Dependency scan in CI (`pip-audit`, `npm audit --omit=dev`).
- [ ] DPDP principles: purpose stated, consent flag for device signals, data retention 180 d for simulated PII.

## 2. Test strategy
| Level | Tool | Scope | Target |
|---|---|---|---|
| Unit (Python) | pytest | services, features, rules, reason map | ≥ 70% coverage on `app/services` |
| Unit (ML) | pytest | feature builders, simulator shapes, model I/O | every module |
| API | pytest + httpx AsyncClient | every endpoint, auth, errors | all P0 endpoints |
| Contract | schemathesis (optional) | OpenAPI fuzz | no 500s |
| Load | Locust | `/transactions/score` | p95 < 100 ms @ 50 rps; 500 rps throughput |
| Web | Vitest + RTL, Playwright | components, e2e flow | key flows |
| Customer app | Vitest + Playwright (mobile viewport) | coach flow, normal payment | key flows |
| ML quality | `ml/pipelines/evaluate.py` | metrics table from spec 03 | all targets |
| Fairness | evaluate.py `--fairness` | FPR by city tier / age band | gap < 2 pts |

Test data: fixtures in `backend/tests/fixtures/`; tiny parquet samples in `ml/tests/fixtures/`.

## 3. CI (GitHub Actions `.github/workflows/ci.yml`)
Jobs: `python-lint` (ruff, mypy) → `python-test` (pytest with services: postgres, redis) → `web` (npm ci, lint, test, build, Playwright) → `audit`.

## 4. Deploy (demo)
- Build web (`npm run build`) → served by Nginx (dashboard and customer app in one build).
- One backend image `backend/Dockerfile` (python:3.11-slim) used twice: `api` (uvicorn, 2 workers) and `worker` (`python -m app.streaming.worker`).
- _(T3.9)_ Gate B was measured on a Windows dev laptop with **one** uvicorn process (Windows forces the selector loop and pins kept-alive connections to one worker with `--workers`). Re-measure on the Linux image with 2 workers at deploy. Load test: `load/prepare.py` (separate `fraudshield_load` DB + Redis db 2), `load/locustfile.py` (Poisson arrivals, 30 s warm-up, 2 min at 50 rps, ramp +25/30 s), `load/analyze.py`. Redis runs AOF only (`--save ""`): RDB snapshots on top forked Redis every minute under load.
- `infra/docker-compose.prod.yml`: nginx, api, worker, postgres, redis on one VM (2 vCPU / 4 GB is enough) or Render.
- Models copied into image from `ml/artifacts` at build time.
- Grafana dashboard JSON in `infra/grafana/dashboards/`.

## 5. Demo script (5 minutes)
1. Overview dashboard with seeded day of data.
2. Start simulator stream → Live Monitor fills green.
3. Customer app (phone or phone frame beside the dashboard): digital-arrest scenario → Scam Coach → HOLD. Dashboard: red alert with reasons in < 1 s.
4. Open case → SHAP waterfall + sentence → Graph Explorer shows payee in a 15-account ring.
5. Confirm fraud → a second customer pays the same VPA → instant "reported by N users" warning.
6. Models page: new label counted; drift report.
7. Close with metrics slide (PR-AUC, latency, scenarios caught).

## 6. Deliverables checklist (for submission)
- [ ] Source repo with README and one-command setup
- [ ] Trained models + model cards
- [ ] Evaluation report (metrics, fairness, latency)
- [ ] Architecture diagram + this docs folder
- [ ] Demo video (5 min) + live demo
- [ ] Final project report / slides
