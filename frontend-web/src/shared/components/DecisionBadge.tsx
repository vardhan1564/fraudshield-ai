import { CircleCheck, OctagonPause, ShieldQuestion } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/shared/lib/cn";
import { RISK_CLASSES, type RiskLevel } from "@/shared/lib/riskColor";
import type { Decision } from "@/shared/types/api";

const LOOK: Record<Decision, { level: RiskLevel; icon: typeof CircleCheck }> = {
  APPROVE: { level: "low", icon: CircleCheck },
  STEP_UP: { level: "medium", icon: ShieldQuestion },
  HOLD: { level: "high", icon: OctagonPause },
};

/** Approved / Extra check / Held: icon + word + the matching risk colour. */
export function DecisionBadge({ decision, className }: { decision: Decision; className?: string }) {
  const { t } = useTranslation();
  const { level, icon: Icon } = LOOK[decision];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[13px] font-medium", className)}>
      {/* colour on the icon only: amber text on white is too faint to read */}
      <Icon className={cn("size-4 shrink-0", RISK_CLASSES[level].text)} aria-hidden="true" />
      {t(`decisions.${decision}`)}
    </span>
  );
}
