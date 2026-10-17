"""Generate the simulated UPI dataset (spec 03 §4).

python -m ml.simulator.run --n 100000 --out ml/data/simulated
"""

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ml.simulator.config import DEFAULT_CONFIG, SimConfig, load_config
from ml.simulator.normal import generate_normal
from ml.simulator.outputs import assemble, write_outputs
from ml.simulator.population import build_population
from ml.simulator.rings import generate_rings
from ml.simulator.scenarios import generate_scenarios
from ml.simulator.schema import SHARED_SCENARIOS

ROOT = Path(__file__).resolve().parents[2]
MIN_NORMAL_SHARE = 0.5  # rings and scenarios may use at most half the rows


def simulate(cfg: SimConfig) -> dict[str, pd.DataFrame]:
    """Build all five tables in memory. Same config and seed -> identical output."""
    # One independent random stream per stage, so changing one stage doesn't shift the others.
    pop, ring, scen, norm, out = (
        np.random.default_rng(s) for s in np.random.SeedSequence(cfg.seed).spawn(5)
    )
    world = build_population(cfg, pop)
    rings = generate_rings(world, ring)
    scenarios = generate_scenarios(world, cfg.n_transactions, scen)
    n_normal = cfg.n_transactions - len(rings) - len(scenarios)
    if n_normal < MIN_NORMAL_SHARE * cfg.n_transactions:
        need = round((len(rings) + len(scenarios)) / (1 - MIN_NORMAL_SHARE))
        raise ValueError(
            f"--n {cfg.n_transactions} is too small for {cfg.rings.count} rings; use --n >= {need}"
        )
    normal = generate_normal(world, n_normal, norm)
    return assemble(world, [rings, scenarios, normal], out)


def summary(tables: dict[str, pd.DataFrame], cfg: SimConfig) -> str:
    txns = tables["transactions"]
    n = len(txns)
    counts = txns["scenario"].value_counts()

    def row(name: str, last: str) -> str:
        k = counts.get(name, 0)
        return f"{name:<22}{k:>7}{k / n:>9.3%}{last:>10}"

    lines = [f"{'file':<22}{'rows':>9}"]
    lines += [f"{name + '.parquet':<22}{len(frame):>9,}" for name, frame in tables.items()]
    lines += ["", f"{'scenario':<22}{'rows':>7}{'share':>9}{'spec':>10}"]
    lines += [row(name, f"{cfg.shares[name]:.3%}") for name in SHARED_SCENARIOS]
    lines.append(row("mule_ring", f"{txns['ring_id'].nunique()} rings"))
    mules = tables["users"]["is_mule"].sum()
    hard = txns["hard_negative"].notna().sum()
    fraud = txns["is_fraud"].mean()
    lines += ["", f"fraud rate {fraud:.2%}  |  hard negatives {hard:,}  |  mules {mules:,}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the simulated UPI dataset (spec 03 §4).")
    parser.add_argument("--n", type=int, help="transaction rows (default: config n_transactions)")
    parser.add_argument("--users", type=int, help="ordinary customers (default: config n_users)")
    parser.add_argument("--seed", type=int, help="random seed (default: config seed)")
    parser.add_argument("--out", type=Path, default=ROOT / "ml" / "data" / "simulated")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)

    cfg = load_config(args.config, n_transactions=args.n, n_users=args.users, seed=args.seed)
    started = time.perf_counter()
    tables = simulate(cfg)
    write_outputs(tables, args.out)
    print(summary(tables, cfg))
    print(f"\nseed {cfg.seed}  |  {time.perf_counter() - started:.1f} s  |  written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
