/** Risk bands from spec 06 §2: green < 0.30, amber 0.30–0.70, red > 0.70. Colour is always
 * paired with a text label, never used alone. */
export type RiskLevel = "low" | "medium" | "high";

export const RISK_MEDIUM_FROM = 0.3;
export const RISK_HIGH_ABOVE = 0.7;

export function riskLevel(score: number): RiskLevel {
  if (score > RISK_HIGH_ABOVE) return "high";
  if (score >= RISK_MEDIUM_FROM) return "medium";
  return "low";
}

/** Tailwind classes per level: soft background, strong text, matching dot. */
export const RISK_CLASSES: Record<RiskLevel, { badge: string; dot: string; text: string }> = {
  low: {
    badge: "bg-risk-low/12 text-risk-low ring-risk-low/25",
    dot: "bg-risk-low",
    text: "text-risk-low",
  },
  medium: {
    badge: "bg-risk-medium/15 text-risk-medium ring-risk-medium/30",
    dot: "bg-risk-medium",
    text: "text-risk-medium",
  },
  high: {
    badge: "bg-risk-high/12 text-risk-high ring-risk-high/25",
    dot: "bg-risk-high",
    text: "text-risk-high",
  },
};

export function riskClasses(score: number) {
  return RISK_CLASSES[riskLevel(score)];
}
