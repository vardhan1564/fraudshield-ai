import { describe, expect, it } from "vitest";

import { slaState } from "@/dashboard/lib/sla";

const created = "2026-10-21T10:00:00Z";
const at = (minutes: number) => Date.parse(created) + minutes * 60_000;

describe("SLA timer", () => {
  it("gives high alerts 15 minutes: ok, then due soon in the last quarter, then overdue", () => {
    expect(slaState(created, "high", at(5))).toEqual({ remainingS: 600, tone: "ok" });
    expect(slaState(created, "high", at(12)).tone).toBe("soon");
    expect(slaState(created, "high", at(16))).toEqual({ remainingS: -60, tone: "overdue" });
  });

  it("gives medium alerts 1 hour and low alerts 4 hours", () => {
    expect(slaState(created, "medium", at(30)).remainingS).toBe(1800);
    expect(slaState(created, "low", at(60)).remainingS).toBe(3 * 3600);
  });
});
