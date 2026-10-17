"""Transaction features: amount, ratio to the 30-day average, time of day, channel, category."""

from datetime import timedelta, timezone

import numpy as np
import pandas as pd

from ml.features.windows import Windows

IST = timezone(timedelta(hours=5, minutes=30))
DAY = 86_400


def txn_features(txns: pd.DataFrame) -> pd.DataFrame:
    amount = txns["amount"].astype("float64")

    # Average of the user's successful payments in the previous 30 days; 1.0 with no history.
    success = (txns["status"] == "SUCCESS").to_numpy()
    win = Windows(txns["user_id"], txns["ts"])
    total = win.sum(amount.to_numpy() * success, 30 * DAY)
    count = win.count(30 * DAY, mask=success)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(count > 0, amount.to_numpy() / (total / np.maximum(count, 1)), 1.0)

    local = txns["ts"].dt.tz_convert(IST)
    hour = local.dt.hour
    channel = txns["channel"]
    return pd.DataFrame(
        {
            "amount": amount,
            "amount_log": np.log1p(amount),
            "amount_ratio_avg": ratio,
            "hour": hour,
            "weekday": local.dt.weekday,
            "is_night": (hour >= 23) | (hour < 5),
            "channel_upi": channel == "UPI",
            "channel_card": channel == "CARD",
            "channel_wallet": channel == "WALLET",
            "merchant_category": txns["merchant_category"],
        },
        index=txns.index,
    )
