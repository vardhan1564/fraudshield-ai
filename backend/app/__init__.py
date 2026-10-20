"""FraudShield API package.

The API shares code with the ML side (feature definitions, model wrappers in `ml/`), so the repo
root must be importable even when uvicorn starts from backend/.
"""

import os
import sys
from pathlib import Path

# One math thread per prediction (T3.9). The API scores one row at a time, many requests in
# parallel; numpy/XGBoost/LightGBM/PyTorch thread pools sized for batches spun on every call and
# took the laptop to 100% CPU at ~40 payments/s. Must run before those libraries are imported.
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)
