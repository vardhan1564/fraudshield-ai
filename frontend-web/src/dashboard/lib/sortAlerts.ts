/** Alert Queue sorting (spec 06 §3). Every key is fixed per alert, so rows don't jump as time passes. */
import { SLA_MINUTES } from "@/dashboard/lib/sla";
import type { AlertItem, Severity } from "@/shared/types/api";

export type SortKey = "severity" | "risk" | "amount" | "age" | "sla";
export type SortDir = "asc" | "desc";
export interface Sort {
  key: SortKey;
  dir: SortDir;
}

/** What a first click on a column means: most severe, riskiest, largest, longest waiting, most urgent. */
export const FIRST_DIR: Record<SortKey, SortDir> = {
  severity: "desc",
  risk: "desc",
  amount: "desc",
  age: "desc",
  sla: "asc",
};

/** The API's own order: most severe first, then oldest. */
export const DEFAULT_SORT: Sort = { key: "severity", dir: "desc" };

const RANK: Record<Severity, number> = { low: 1, medium: 2, high: 3 };

function value(a: AlertItem, key: SortKey): number {
  const created = Date.parse(a.created_at);
  switch (key) {
    case "severity":
      return RANK[a.severity];
    case "risk":
      return a.risk_score ?? -1;
    case "amount":
      return a.amount;
    case "age":
      return -created; // larger = waiting longer
    case "sla":
      return created + SLA_MINUTES[a.severity] * 60_000; // due time
  }
}

export function sortAlerts(alerts: readonly AlertItem[], sort: Sort): AlertItem[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  return [...alerts].sort(
    (a, b) =>
      sign * (value(a, sort.key) - value(b, sort.key)) ||
      Date.parse(a.created_at) - Date.parse(b.created_at), // ties: oldest first
  );
}

export function nextSort(current: Sort, key: SortKey): Sort {
  if (current.key !== key) return { key, dir: FIRST_DIR[key] };
  return { key, dir: current.dir === "asc" ? "desc" : "asc" };
}
