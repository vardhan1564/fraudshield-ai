"""Gate B report from a locust run (T3.9).

    python load/analyze.py load/reports/<run>_requests.csv

Times are relative to the end of the 30 s warm-up (negative t = warm-up, ignored). Stage 1 is
the gate (120 s at 50 rps); then one row per 30 s ramp step. Max sustained
throughput = the highest achieved rps of a step with p95 < 100 ms and < 1% errors. Writes
load/reports/<run>_gate_b.md next to the CSV and prints it.
"""

import sys
from pathlib import Path

import pandas as pd

GATE_S, STEP_S, GATE_RPS, STEP_RPS = 120, 30, 50, 25
WARMUP_S = 5  # the first seconds include connection set-up and the spawn
P95_LIMIT_MS = 100.0


def summarise(part: pd.DataFrame, seconds: float) -> dict[str, float]:
    ok = part[part["status"] == 200]
    return {
        "requests": len(part),
        "rps": len(part) / seconds,
        "errors_pct": 100 * (1 - len(ok) / len(part)) if len(part) else 0.0,
        "p50": part["client_ms"].quantile(0.50),
        "p95": part["client_ms"].quantile(0.95),
        "p99": part["client_ms"].quantile(0.99),
        "server_p95": pd.to_numeric(ok["server_ms"]).quantile(0.95) if len(ok) else float("nan"),
    }


HEADER = ("Stage", "Target rps", "Achieved rps", "Requests", "Errors", "p50 ms", "p95 ms",
          "p99 ms", "Server p95 ms")  # fmt: skip


def row(stage: str, target: int, s: dict[str, float]) -> str:
    cells = [stage, str(target), f"{s['rps']:.1f}", f"{int(s['requests']):,}",
             f"{s['errors_pct']:.2f}%", f"{s['p50']:.1f}", f"{s['p95']:.1f}", f"{s['p99']:.1f}",
             f"{s['server_p95']:.1f}"]  # fmt: skip
    return "| " + " | ".join(cells) + " |"


def main(path: Path) -> str:
    df = pd.read_csv(path)
    gate = summarise(df[(df["t"] >= WARMUP_S) & (df["t"] < GATE_S)], GATE_S - WARMUP_S)
    passed = (
        gate["p95"] < P95_LIMIT_MS and gate["errors_pct"] < 1 and gate["rps"] >= 0.95 * GATE_RPS
    )

    steps = []
    end = df["t"].max()
    start, target = GATE_S, GATE_RPS + STEP_RPS
    while start + STEP_S <= end + 1:
        part = df[(df["t"] >= start + 5) & (df["t"] < start + STEP_S)]  # skip the spawn
        if len(part):
            steps.append({"target": target, **summarise(part, STEP_S - 5)})
        start, target = start + STEP_S, target + STEP_RPS
    healthy = [s for s in steps if s["p95"] < P95_LIMIT_MS and s["errors_pct"] < 1]
    best = max(healthy, key=lambda s: s["rps"]) if healthy else None
    peak = max(steps, key=lambda s: s["rps"]) if steps else None

    lines = [
        f"# Gate B: {path.stem.removesuffix('_requests')}",
        "",
        f"**Gate B (p95 < 100 ms at 50 rps for 2 min): {'PASS' if passed else 'FAIL'}**",
        "",
        "| " + " | ".join(HEADER) + " |",
        "|" + "---|" * len(HEADER),
        row("Gate (5-120 s)", GATE_RPS, gate),
        *(row("Ramp", int(st["target"]), st) for st in steps),
    ]
    lines += [
        "",
        "**Max sustained throughput** (p95 < 100 ms, < 1% errors): "
        + (
            f"{best['rps']:.1f} rps (target {best['target']})"
            if best
            else "below the first ramp step"
        ),
        "",
        "**Peak achieved**: "
        + (f"{peak['rps']:.1f} rps (p95 {peak['p95']:.0f} ms)" if peak else "-"),
        "",
        "A 30 s warm-up at 25 rps runs first and is not counted. "
        "Client times are measured by locust on the same laptop (they include its own overhead); "
        "server times are the API's `latency_ms`.",
    ]
    report = "\n".join(lines) + "\n"
    out = path.with_name(path.name.replace("_requests.csv", "_gate_b.md"))
    out.write_text(report, encoding="utf-8")
    return report


if __name__ == "__main__":
    print(main(Path(sys.argv[1])))
