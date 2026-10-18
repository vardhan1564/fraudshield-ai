"""Time-based train / validation / test split (spec 03 §5): never random; test is the future."""

from typing import NamedTuple

import numpy as np
import pandas as pd


class Split(NamedTuple):
    train: np.ndarray  # row positions into the input frame
    val: np.ndarray
    test: np.ndarray


def time_split(ts: pd.Series, fractions: tuple[float, float, float] = (0.70, 0.15, 0.15)) -> Split:
    """Positions of the earliest 70% (train), the next 15% (val) and the latest 15% (test).

    Rows are ordered by time (stable for ties), so no training row is later than a validation
    row and no validation row is later than a test row.
    """
    if not np.isclose(sum(fractions), 1.0) or min(fractions) <= 0:
        raise ValueError(f"fractions must be positive and sum to 1, got {fractions}")
    order = np.argsort(ts.to_numpy(), kind="stable")
    n = len(order)
    n_train = int(round(n * fractions[0]))
    n_val = int(round(n * fractions[1]))
    return Split(
        train=order[:n_train],
        val=order[n_train : n_train + n_val],
        test=order[n_train + n_val :],
    )
