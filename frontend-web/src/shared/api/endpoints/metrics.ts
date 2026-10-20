import { get } from "@/shared/api/client";
import type { MetricsOverview } from "@/shared/types/api";

/** Default window: the last 24 hours (max 92 days). */
export function metricsOverview(range: { from?: string; to?: string } = {}): Promise<MetricsOverview> {
  return get<MetricsOverview>("/metrics/overview", { params: range });
}
