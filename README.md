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

## Everyday commands (repo root, venv active)
| Task | Command |
|---|---|
| Lint | `ruff check .` (auto-fix: `ruff check . --fix`) |
| Format | `ruff format .` |
| Type check | `mypy` (strict on `backend/app`) |
| All tests | `pytest -q` |
| Backend tests only | `cd backend; pytest -q` |

More commands (API, worker, training, web) are added here as each part is built.
