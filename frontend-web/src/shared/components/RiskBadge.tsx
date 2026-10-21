import { useTranslation } from "react-i18next";

import { cn } from "@/shared/lib/cn";
import { RISK_CLASSES, riskLevel } from "@/shared/lib/riskColor";

/** Colour + text, never colour alone (spec 06 §2). Analysts see the score; customers never do. */
export function RiskBadge({
  score,
  showScore = true,
  className,
}: {
  score: number;
  showScore?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const level = riskLevel(score);
  const classes = RISK_CLASSES[level];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
        classes.badge,
        className,
      )}
    >
      <span className={cn("size-1.5 rounded-full", classes.dot)} aria-hidden="true" />
      {t(`severity.${level}`)}
      {showScore && <span className="font-medium tabular-nums opacity-80">{score.toFixed(2)}</span>}
    </span>
  );
}
