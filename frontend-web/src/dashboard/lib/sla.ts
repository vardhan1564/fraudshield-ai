/**
 * Time an analyst has to pick up an alert, by severity (decision log, T4.3: the spec named an
 * SLA timer but no times). "Due soon" in the last quarter, "Overdue" after.
 */
import type { Severity } from "@/shared/types/api";

export const SLA_MINUTES: Record<Severity, number> = { high: 15, medium: 60, low: 240 };

export type SlaTone = "ok" | "soon" | "overdue";

export interface SlaState {
  /** Seconds left; negative once overdue. */
  remainingS: number;
  tone: SlaTone;
}

export function slaState(createdAt: string, severity: Severity, nowMs: number): SlaState {
  const totalS = SLA_MINUTES[severity] * 60;
  const remainingS = (Date.parse(createdAt) + totalS * 1000 - nowMs) / 1000;
  const tone: SlaTone = remainingS <= 0 ? "overdue" : remainingS <= totalS / 4 ? "soon" : "ok";
  return { remainingS, tone };
}
