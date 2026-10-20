export type RangeKey = "24h" | "7d" | "30d";

/** Overview time-range presets (the API allows up to 92 days). */
export const RANGE_MS: Record<RangeKey, number> = {
  "24h": 24 * 3_600_000,
  "7d": 7 * 86_400_000,
  "30d": 30 * 86_400_000,
};
