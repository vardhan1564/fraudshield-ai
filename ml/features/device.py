"""Device and network features: new or rooted phone, distance from home, VPN, recent SIM change."""

import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: pd.Series, lon1: pd.Series, lat2: pd.Series, lon2: pd.Series) -> np.ndarray:
    p1, p2 = np.radians(lat1.to_numpy(float)), np.radians(lat2.to_numpy(float))
    dlat = p2 - p1
    dlon = np.radians(lon2.to_numpy(float) - lon1.to_numpy(float))
    a = np.sin(dlat / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def device_features(txns: pd.DataFrame, users: pd.DataFrame, devices: pd.DataFrame) -> pd.DataFrame:
    ts = txns["ts"]
    dev = devices.set_index("device_id")
    first_seen = txns["device_id"].map(dev["first_seen"])
    # New: first seen less than 24 h before the payment, or never seen at all.
    device_is_new = first_seen.isna() | ((ts - first_seen) < pd.Timedelta(hours=24))
    rooted = txns["device_id"].map(dev["is_rooted"]).fillna(False).astype(bool)

    profile = users.set_index("user_id")
    distance = haversine_km(
        txns["geo_lat"],
        txns["geo_lon"],
        txns["user_id"].map(profile["home_lat"]),
        txns["user_id"].map(profile["home_lon"]),
    )
    # SIM changed in the 7 days up to the payment; a later change (or none, NaT) is False.
    sim = txns["user_id"].map(profile["sim_changed_at"])
    sim_changed_7d = (sim <= ts) & ((ts - sim) <= pd.Timedelta(days=7))

    return pd.DataFrame(
        {
            "device_is_new": device_is_new.to_numpy(bool),
            "device_rooted": rooted.to_numpy(),
            "ip_distance_km": np.nan_to_num(distance, nan=0.0),  # unknown home -> 0
            "is_vpn": txns["is_vpn"].astype(bool).to_numpy(),
            "sim_changed_7d": sim_changed_7d.fillna(False).to_numpy(bool),
        },
        index=txns.index,
    )
