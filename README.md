# FraudShield AI

Real-time fraud detection for Indian digital payments (UPI, card, wallet). It scores every
transaction in under 100 ms, explains each decision in plain language, finds mule-account rings
with graph ML, and steps in during social-engineering scams before the money moves.

Everything is simulated: no real banks or payment rails are involved.

- Product: [`docs/PRD.md`](docs/PRD.md)
- Progress: [`docs/PROGRESS.md`](docs/PROGRESS.md)
- Specs: [`docs/specs/`](docs/specs/)

## Layout
| Folder | What |
|---|---|
| `backend/` | FastAPI API + worker (`app/`), Alembic migrations, tests |
| `ml/` | Simulator, feature builders, models, training pipelines, notebooks, tests |
| `frontend-web/` | One React project: analyst dashboard (`/dashboard`) + customer app (`/app`) |
| `infra/` | Docker Compose for PostgreSQL, Redis and MLflow |
| `load/` | Locust load tests |
| `scripts/` | Data download, seeding, demo |

## Prerequisites
| Tool | Version | Check |
|---|---|---|
| Git | recent | `git --version` |
| Python | 3.11 | `python --version` (Windows: `py -3.11 --version`) |
| Node.js | 20 LTS or newer | `node --version` |
| Docker Desktop | latest | `docker info` prints a server version |
| Kaggle CLI | latest | `kaggle --version` (needed from task T1.1) |

## Setup

### Windows (native PowerShell)
```powershell
git clone <repo-url> fraudshield-ai
cd fraudshield-ai
Copy-Item .env.example .env          # then fill in SECRET_KEY and ANTHROPIC_API_KEY

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1         # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
pip install -r requirements-dev.txt  # lint + test tools
pip install -r ml/requirements.txt   # full stack (backend runtime + ML); large, ~2 GB
```
Docker Desktop must show **Engine running** before you start the infrastructure.
WSL2 also works: follow the macOS/Linux steps inside an Ubuntu shell.

### macOS / Linux
```bash
git clone <repo-url> fraudshield-ai
cd fraudshield-ai
cp .env.example .env                 # then fill in SECRET_KEY and ANTHROPIC_API_KEY

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -r ml/requirements.txt
```

### Requirements files
| File | Contents |
|---|---|
| `requirements-dev.txt` | ruff, mypy, pytest (enough for lint and the current tests) |
| `backend/requirements.txt` | API + worker runtime, including the model libraries used for scoring |
| `ml/requirements.txt` | Everything in the backend file plus training, tracking, drift and notebooks |

PyTorch installs from the CPU-only wheel index (set at the top of `backend/requirements.txt`).

## Local infrastructure
PostgreSQL 16, Redis 7 and MLflow run in Docker; the API, worker and web app run on your machine.
Run these from the repo root (Docker Desktop must show **Engine running**):

| Task | Command |
|---|---|
| Start (waits until healthy) | `docker compose --env-file .env -f infra/docker-compose.yml up -d --wait` |
| Status | `docker compose -f infra/docker-compose.yml ps` |
| Logs | `docker compose -f infra/docker-compose.yml logs -f mlflow` |
| Stop (keeps data) | `docker compose -f infra/docker-compose.yml down` |

| Service | Address | Data |
|---|---|---|
| PostgreSQL | `localhost:5432` (user/db/password from `.env`) | named volume `postgres-data` |
| Redis | `localhost:6379` (append-only file on) | named volume `redis-data` |
| MLflow UI | http://localhost:5000 | `./mlruns` in the repo (git-ignored) |

Ports listen on `127.0.0.1` only. `--env-file .env` makes Compose read the root `.env`; without it the
defaults from `.env.example` are used. Postgres reads its user and password only the first time
it creates the volume, so if you change them in `.env` later, the old ones still apply.
`down -v` resets them by **deleting the volumes and all their data**.

## Datasets
Raw data goes to `ml/data/raw/<name>/` (git-ignored, about 3 GB in total).

1. Kaggle token: kaggle.com → Settings → **API Tokens** → **Generate New Token**, then save the token
   text as `%USERPROFILE%\.kaggleccess_token` (Windows) or `~/.kaggle/access_token` (macOS/Linux).
   A legacy `kaggle.json` in the same folder also works.
2. IEEE-CIS is a competition: open https://www.kaggle.com/competitions/ieee-fraud-detection/rules
   once and accept the rules, or its download fails with 403.
3. Download (needs `pip install -r ml/requirements.txt` for the Kaggle CLI):
   ```powershell
   # Windows: use Git Bash, not the WSL bash in System32
   & "C:\Program Files\Git\bin\bash.exe" scripts/download_data.sh            # all four
   & "C:\Program Files\Git\bin\bash.exe" scripts/download_data.sh ieee       # just one
   ```
   macOS/Linux: `bash scripts/download_data.sh`. Finished datasets are skipped on re-runs.

| Name | Kaggle source | Used for |
|---|---|---|
| `ieee` | competition `ieee-fraud-detection` | train + benchmark `txn_model` |
| `ulb` | `mlg-ulb/creditcardfraud` | quick baseline |
| `paysim` | `ealaxi/paysim1` | mobile-money patterns |
| `elliptic` | `ellipticco/elliptic-data-set` | graph model pre-training |

EDA: `jupyter lab ml/notebooks/01_eda_ieee.ipynb` (reads ~1 GB into memory as float32).

### Simulated UPI data
```powershell
python -m ml.simulator.run --n 100000            # writes 5 parquet files to ml/data/simulated/
python -m ml.simulator.run --n 20000 --seed 7    # smaller, different random data
```
Takes about 10 s for 100k rows. Settings (scenario shares, rings, cities) are in `ml/simulator/config.yaml`;
the same seed always gives the same data.

### Features
```powershell
python -m ml.features.build    # ml/data/simulated -> ml/data/features/simulated.parquet (38 features + labels)
```
Feature names and types live in `ml/features/schema.py`; after changing it, run
`python -m ml.features.schema` to regenerate `ml/features/features.json`.

### Training (MLflow must be running: see Local infrastructure)
```powershell
python -m ml.pipelines.train --dataset simulated   # ~10 s
python -m ml.pipelines.train --dataset ieee        # ~15 min, needs ~2 GB free RAM
```
Settings are in `ml/pipelines/config.yaml`. Runs appear in MLflow (http://127.0.0.1:5000) under the
`txn_model` experiment; add `--no-mlflow` to just print the metrics table.

Tuning (Optuna, 30 trials) saves the served model to `ml/artifacts/txn_model.json` (+ `.meta.json`),
logs a SHAP summary plot to MLflow and rewrites `ml/models/txn_model/MODEL_CARD.md`:
```powershell
python -m ml.pipelines.tune --dataset simulated --trials 30   # ~2 min
```

Anomaly model (legit data only; keeps the better of Isolation Forest / autoencoder on account
takeover) to `ml/artifacts/anomaly_model.pt`, git-ignored, so re-run it after a fresh clone:
```powershell
python -m ml.models.anomaly_model   # ~15 s
```

Social-engineering model (LightGBM; simulated Scam Coach answers) to `ml/artifacts/social_model.txt`:
```powershell
python -m ml.models.social_model    # ~10 s
```

## Everyday commands (repo root, venv active)
| Task | Command |
|---|---|
| Lint | `ruff check .` (auto-fix: `ruff check . --fix`) |
| Format | `ruff format .` |
| Type check | `mypy` (strict on `backend/app`) |
| All tests | `pytest -q` |
| Backend tests only | `cd backend; pytest -q` |

More commands (API, worker, training, web) are added here as each part is built.
