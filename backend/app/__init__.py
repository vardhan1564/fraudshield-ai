"""FraudShield API package.

The API shares code with the ML side (feature definitions, model wrappers in `ml/`), so the repo
root must be importable even when uvicorn starts from backend/.
"""

import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)
