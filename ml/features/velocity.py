"""Velocity features: the user's earlier attempts (failed ones too) in sliding windows."""

import pandas as pd

from ml.features.windows import Windows

MINUTE, HOUR, DAY = 60, 3_600, 86_400


def velocity_features(txns: pd.DataFrame) -> pd.DataFrame:
    win = Windows(txns["user_id"], txns["ts"])
    amount = txns["amount"].astype("float64").to_numpy()
    failed = (txns["status"] == "FAILED").to_numpy()
    return pd.DataFrame(
        {
            "txn_count_5m": win.count(5 * MINUTE),
            "txn_count_1h": win.count(HOUR),
            "txn_count_24h": win.count(DAY),
            "amount_sum_1h": win.sum(amount, HOUR).round(2),
            "amount_sum_24h": win.sum(amount, DAY).round(2),
            "failed_count_1h": win.count(HOUR, mask=failed),
        },
        index=txns.index,
    )
