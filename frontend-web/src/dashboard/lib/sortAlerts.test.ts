import { describe, expect, it } from "vitest";

import { DEFAULT_SORT, nextSort, sortAlerts } from "@/dashboard/lib/sortAlerts";
import type { AlertItem, Severity } from "@/shared/types/api";

function alert(id: string, severity: Severity, minutesAgo: number, amount = 1000, risk = 0.5): AlertItem {
  return {
    alert_id: id, case_id: `c-${id}`, txn_id: `T-${id}`, severity, status: "open", assigned_to: null,
    risk_score: risk, decision: "HOLD", amount, channel: "UPI", top_reasons: [],
    created_at: new Date(Date.parse("2026-10-21T10:00:00Z") - minutesAgo * 60_000).toISOString(),
  };
}

const ids = (rows: AlertItem[]) => rows.map((r) => r.alert_id);

describe("alert sorting", () => {
  const rows = [
    alert("a", "medium", 5, 50_000, 0.6),
    alert("b", "high", 2, 20_000, 0.95),
    alert("c", "high", 10, 90_000, 0.8),
    alert("d", "low", 30, 5_000, 0.4),
  ];

  it("defaults to the API order: most severe first, then oldest", () => {
    expect(ids(sortAlerts(rows, DEFAULT_SORT))).toEqual(["c", "b", "a", "d"]);
  });

  it("sorts by amount, risk and waiting time", () => {
    expect(ids(sortAlerts(rows, { key: "amount", dir: "desc" }))).toEqual(["c", "a", "b", "d"]);
    expect(ids(sortAlerts(rows, { key: "risk", dir: "asc" }))).toEqual(["d", "a", "c", "b"]);
    expect(ids(sortAlerts(rows, { key: "age", dir: "desc" }))).toEqual(["d", "c", "a", "b"]);
  });

  it("sorts by SLA: the alert due first comes first", () => {
    // due: c in 5 min, b in 13, a in 55, d in 210
    expect(ids(sortAlerts(rows, { key: "sla", dir: "asc" }))).toEqual(["c", "b", "a", "d"]);
  });

  it("a click on a new column picks its natural order; a second click flips it", () => {
    expect(nextSort(DEFAULT_SORT, "sla")).toEqual({ key: "sla", dir: "asc" });
    expect(nextSort({ key: "sla", dir: "asc" }, "sla")).toEqual({ key: "sla", dir: "desc" });
  });
});
