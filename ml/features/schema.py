"""Feature store contract (spec 03 §2): exact names, dtypes and groups, in model input order.

`python -m ml.features.schema` rewrites features.json, which MLflow logs with every model and
the backend's online feature service reads.
"""

import json
from pathlib import Path
from typing import NamedTuple

FEATURES_JSON = Path(__file__).with_name("features.json")


class Feature(NamedTuple):
    name: str
    dtype: str  # pandas dtype: float64 | int64 | bool | category
    group: str


# Fixed so the category codes never change between training runs and online scoring.
MERCHANT_CATEGORIES = (
    "P2P",
    "GROCERY",
    "FOOD",
    "FUEL",
    "UTILITIES",
    "ECOMMERCE",
    "TRAVEL",
    "ENTERTAINMENT",
    "HEALTH",
    "EDUCATION",
    "RENT",
)

FEATURES = [
    Feature("amount", "float64", "txn"),
    Feature("amount_log", "float64", "txn"),
    Feature("amount_ratio_avg", "float64", "txn"),
    Feature("hour", "int64", "txn"),
    Feature("weekday", "int64", "txn"),
    Feature("is_night", "bool", "txn"),
    Feature("channel_upi", "bool", "txn"),
    Feature("channel_card", "bool", "txn"),
    Feature("channel_wallet", "bool", "txn"),
    Feature("merchant_category", "category", "txn"),
    Feature("txn_count_5m", "int64", "velocity"),
    Feature("txn_count_1h", "int64", "velocity"),
    Feature("txn_count_24h", "int64", "velocity"),
    Feature("amount_sum_1h", "float64", "velocity"),
    Feature("amount_sum_24h", "float64", "velocity"),
    Feature("failed_count_1h", "int64", "velocity"),
    Feature("payee_is_new", "bool", "payee"),
    Feature("payee_account_age_days", "int64", "payee"),
    Feature("payee_unique_senders_24h", "int64", "payee"),
    Feature("payee_reputation", "float64", "payee"),
    Feature("payee_report_count", "int64", "payee"),
    Feature("device_is_new", "bool", "device"),
    Feature("device_rooted", "bool", "device"),
    Feature("ip_distance_km", "float64", "device"),
    Feature("is_vpn", "bool", "device"),
    Feature("sim_changed_7d", "bool", "device"),
    Feature("typing_speed_z", "float64", "behaviour"),
    Feature("session_len_s", "float64", "behaviour"),
    Feature("login_to_pay_s", "float64", "behaviour"),
    Feature("ctx_unknown_call", "bool", "context"),
    Feature("ctx_screen_share", "bool", "context"),
    Feature("ctx_call_long", "bool", "context"),
    Feature("coach_red_flags", "int64", "coach"),
    Feature("graph_score", "float64", "graph"),
    Feature("graph_degree", "float64", "graph"),
    Feature("graph_pagerank", "float64", "graph"),
    Feature("hops_to_fraud", "float64", "graph"),
    Feature("community_fraud_ratio", "float64", "graph"),
]

FEATURE_NAMES = [f.name for f in FEATURES]
DTYPES = {f.name: f.dtype for f in FEATURES}

# Kept next to the features for training and evaluation, never fed to a model.
LABEL_COLUMNS = ["ts", "is_fraud", "scenario"]


def to_json() -> dict[str, object]:
    return {
        "version": 1,
        "features": [f._asdict() for f in FEATURES],
        "merchant_categories": list(MERCHANT_CATEGORIES),
    }


def write_json(path: Path = FEATURES_JSON) -> None:
    path.write_text(json.dumps(to_json(), indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write_json()
    print(f"wrote {FEATURES_JSON} ({len(FEATURES)} features)")
