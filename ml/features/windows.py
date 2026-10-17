"""Look-back windows per key (user or payee) that only see strictly earlier rows.

Rows are sorted once by (key, time) into a single int64 sort key, so each window is two binary
searches and a difference of cumulative sums: O(n log n), no Python loop per row.
"""

import numpy as np
import pandas as pd


def seconds(ts: pd.Series) -> np.ndarray:
    """Timestamps as int64 epoch seconds."""
    return (ts.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[s]")).astype("int64")


class Windows:
    """Sums over (t - window, t) per key, both ends open: a row exactly `window` earlier and rows
    with the same timestamp (including the row itself) are left out."""

    def __init__(
        self,
        keys: pd.Series,
        ts: pd.Series,
        *,
        query_keys: pd.Series | None = None,
        query_ts: pd.Series | None = None,
    ) -> None:
        # Events are (keys, ts); queries default to the same rows.
        query_keys = keys if query_keys is None else query_keys
        query_ts = ts if query_ts is None else query_ts
        codes, uniques = pd.factorize(pd.concat([keys, query_keys], ignore_index=True))
        t = np.concatenate([seconds(ts), seconds(query_ts)])
        t = t - t.min() if len(t) else t
        span = int(t.max()) + 1 if len(t) else 1
        self._span = span
        key = codes.astype("int64") * span + t
        n = len(keys)
        self._order = np.argsort(key[:n], kind="stable")
        self._sorted = key[:n][self._order]
        self._query = key[n:]
        self._query_t = t[n:]

    def _bounds(self, window_s: int | None) -> tuple[np.ndarray, np.ndarray]:
        hi = np.searchsorted(self._sorted, self._query, side="left")  # strictly earlier
        # First row of the same key; a window must never reach back into the previous key.
        lo = np.searchsorted(self._sorted, self._query - self._query_t, side="left")
        if window_s is not None:
            after = np.searchsorted(self._sorted, self._query - window_s, side="right")
            lo = np.maximum(lo, after)  # strictly after t - window
        return lo, hi

    def sum(self, values: pd.Series | np.ndarray, window_s: int | None) -> np.ndarray:
        """Sum of `values` (aligned with the event rows) inside each query's window."""
        v = np.asarray(values, dtype="float64")[self._order]
        csum = np.concatenate([[0.0], np.cumsum(v)])
        lo, hi = self._bounds(window_s)
        return csum[hi] - csum[lo]

    def count(self, window_s: int | None, mask: pd.Series | np.ndarray | None = None) -> np.ndarray:
        """Number of event rows (optionally only where mask is True) inside each window."""
        if mask is None:
            lo, hi = self._bounds(window_s)
            return (hi - lo).astype("int64")
        return np.rint(self.sum(np.asarray(mask, dtype="float64"), window_s)).astype("int64")
