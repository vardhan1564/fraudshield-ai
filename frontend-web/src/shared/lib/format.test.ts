import { describe, expect, it } from "vitest";

import {
  formatDateTime,
  formatDuration,
  formatInr,
  formatNumber,
  formatPercent,
  maskPhone,
  maskVpa,
} from "@/shared/lib/format";
import { riskLevel } from "@/shared/lib/riskColor";

describe("format", () => {
  it("formats rupees with Indian grouping", () => {
    expect(formatInr(48_500)).toBe("₹48,500");
    expect(formatInr(125_000.5)).toBe("₹1,25,000.5");
    expect(formatNumber(1_250_000)).toBe("12,50,000");
  });

  it("masks phones and VPAs (spec 06 §2, CLAUDE.md rule 8)", () => {
    expect(maskPhone("9812345621")).toBe("98xxxxxx21");
    expect(maskPhone("+91 98123 45621")).toBe("98xxxxxx21");
    expect(maskPhone("98xxxxxx21")).toBe("98xxxxxx21"); // already masked by the API: kept
    expect(maskVpa("refund.help@okbank")).toBe("re***@okbank");
    expect(maskVpa("a@ybl")).toBe("a***@ybl");
    expect(maskPhone(null)).toBe("");
  });

  it("shows dates in IST and durations and percents", () => {
    expect(formatDateTime("2026-10-15T20:44:05Z")).toMatch(/16 Oct.*02:14/);
    expect(formatDuration(245)).toBe("4m 05s");
    expect(formatDuration(9)).toBe("9s");
    expect(formatPercent(0.0064, 2)).toBe("0.64%");
  });
});

describe("riskColor", () => {
  it("uses the spec 06 bands: < 0.30 low, 0.30-0.70 medium, > 0.70 high", () => {
    expect([0, 0.29, 0.3, 0.7, 0.71, 1].map(riskLevel)).toEqual(["low", "low", "medium", "medium", "high", "high"]);
  });
});
