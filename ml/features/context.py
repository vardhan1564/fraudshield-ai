"""Behaviour, call/screen-share context (spec 01 F2), Scam Coach and graph features."""

import pandas as pd

GRAPH_FEATURES = [
    "graph_score",
    "graph_degree",
    "graph_pagerank",
    "hops_to_fraud",
    "community_fraud_ratio",
]


def context_features(txns: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "typing_speed_z": txns["typing_speed_z"].astype("float64"),
            "session_len_s": txns["session_len_s"].astype("float64"),
            "login_to_pay_s": txns["login_to_pay_s"].astype("float64"),
            "ctx_unknown_call": txns["on_call"].astype(bool)
            & ~txns["caller_in_contacts"].astype(bool),
            # A remote-access app (AnyDesk, ...) shows the screen to someone, like screen share.
            "ctx_screen_share": txns["screen_share_active"].astype(bool)
            | txns["remote_access_app"].notna(),
            "ctx_call_long": txns["call_duration_s"] > 300,
            "coach_red_flags": 0,  # no coach session exists before the payment is scored
        },
        index=txns.index,
    )
    for name in GRAPH_FEATURES:  # filled from the graph cache in phase 5 (T5.1)
        frame[name] = 0.0
    return frame
