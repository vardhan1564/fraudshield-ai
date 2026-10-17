"""Payee features: new payee, account age, distinct senders, crowd reports and reputation."""

import numpy as np
import pandas as pd

from ml.features.windows import Windows, distinct_count

DAY = 86_400


def payee_features(txns: pd.DataFrame, reports: pd.DataFrame) -> pd.DataFrame:
    # New payee: this user has no earlier *successful* payment to it (failed tries don't count).
    pair = txns["user_id"].astype(str) + "\x1f" + txns["payee_vpa"].astype(str)
    success = (txns["status"] == "SUCCESS").to_numpy()
    paid_before = Windows(pair, txns["ts"]).count(None, mask=success)

    # Reports filed before the payment; reports filed later don't exist yet at scoring time.
    report_count = Windows(
        reports["payee_vpa"], reports["ts"], query_keys=txns["payee_vpa"], query_ts=txns["ts"]
    ).count(None)
    # Spec 01 F7: min(1, 0.1*report_count + 0.5*confirmed_fraud + 0.3*graph_flag). Confirmed
    # cases and the graph flag come with T5.5 / phase 5; offline they would leak labels.
    reputation = np.minimum(1.0, 0.1 * report_count)

    return pd.DataFrame(
        {
            "payee_is_new": paid_before == 0,
            "payee_account_age_days": txns["payee_account_age_days"].astype("int64"),
            "payee_unique_senders_24h": distinct_count(
                txns["payee_vpa"], txns["user_id"], txns["ts"], DAY
            ),
            "payee_reputation": reputation,
            "payee_report_count": report_count,
        },
        index=txns.index,
    )
