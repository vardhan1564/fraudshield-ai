import type { Reason } from "@/shared/types/api";

export const WATERFALL_SIZE = 10;

export interface WaterfallStep {
  key: string;
  label: string;
  feature: string;
  value: Reason["value"] | null;
  impact: number;
  /** [low, high] of the bar on the log-odds axis: a range bar from the running total. */
  range: [number, number];
  kind: "raises" | "lowers" | "total";
}

/**
 * SHAP waterfall (spec 06 Case Detail): the 10 biggest pushes by |impact|, each bar starting
 * where the previous one ended, then the net effect of them all. `impact` is meta-model log-odds
 * (spec 04 §2.3), so the bars add up.
 */
export function waterfallSteps(shap: readonly Reason[], totalLabel: string): WaterfallStep[] {
  const top = [...shap].sort((a, b) => Math.abs(b.impact) - Math.abs(a.impact)).slice(0, WATERFALL_SIZE);
  let running = 0;
  const steps: WaterfallStep[] = top.map((r, i) => {
    const start = running;
    running += r.impact;
    return {
      key: `${r.feature}-${i}`,
      label: r.text,
      feature: r.feature,
      value: r.value,
      impact: r.impact,
      range: [Math.min(start, running), Math.max(start, running)],
      kind: r.impact >= 0 ? "raises" : "lowers",
    };
  });
  if (steps.length === 0) return steps;
  return [
    ...steps,
    {
      key: "total",
      label: totalLabel,
      feature: "total",
      value: null,
      impact: running,
      range: [Math.min(0, running), Math.max(0, running)],
      kind: "total",
    },
  ];
}

/** "+1.20" / "−0.35": the sign is part of the meaning, so it is always shown. */
export function formatImpact(impact: number): string {
  const fixed = Math.abs(impact).toFixed(2);
  return impact < 0 ? `−${fixed}` : `+${fixed}`;
}
