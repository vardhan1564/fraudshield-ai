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

1. Kaggle token: kaggle.com → Settings → API → **Create New Token**, then save `kaggle.json` to
   `%USERPROFILE%\.kaggle\` (Windows) or `~/.kaggle/` (macOS/Linux).
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

## Everyday commands (repo root, venv active)
| Task | Command |
|---|---|
| Lint | `ruff check .` (auto-fix: `ruff check . --fix`) |
| Format | `ruff format .` |
| Type check | `mypy` (strict on `backend/app`) |
| All tests | `pytest -q` |
| Backend tests only | `cd backend; pytest -q` |

More commands (API, worker, training, web) are added here as each part is built.
