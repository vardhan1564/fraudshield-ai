import { describe, expect, it } from "vitest";

import { formatImpact, waterfallSteps } from "@/dashboard/lib/waterfall";
import type { Reason } from "@/shared/types/api";

const r = (feature: string, impact: number): Reason => ({ feature, value: 1, impact, text: feature });

describe("waterfallSteps", () => {
  it("chains each bar from the running total and ends with the net effect", () => {
    const steps = waterfallSteps([r("a", 1.5), r("b", -0.5), r("c", 0.25)], "Net");
    expect(steps.map((s) => s.range)).toEqual([[0, 1.5], [1, 1.5], [1, 1.25], [0, 1.25]]);
    expect(steps.map((s) => s.kind)).toEqual(["raises", "lowers", "raises", "total"]);
    expect(steps.at(-1)).toMatchObject({ label: "Net", impact: 1.25 });
  });

  it("keeps the 10 biggest pushes in either direction, biggest first", () => {
    const shap = Array.from({ length: 14 }, (_, i) => r(`f${i}`, (i % 2 ? -1 : 1) * (i + 1) * 0.1));
    const steps = waterfallSteps(shap, "Net");
    expect(steps).toHaveLength(11);
    expect(steps[0]?.feature).toBe("f13");
    expect(steps.slice(0, 10).map((s) => s.feature)).not.toContain("f0");
  });

  it("is empty without SHAP values", () => {
    expect(waterfallSteps([], "Net")).toEqual([]);
  });
});

describe("formatImpact", () => {
  it("always shows the sign", () => {
    expect(formatImpact(1.2)).toBe("+1.20");
    expect(formatImpact(-0.354)).toBe("−0.35");
  });
});
