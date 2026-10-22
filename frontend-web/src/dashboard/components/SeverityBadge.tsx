import { useTranslation } from "react-i18next";

import { cn } from "@/shared/lib/cn";
import { RISK_CLASSES } from "@/shared/lib/riskColor";
import type { Severity } from "@/shared/types/api";

/** Alert severity: coloured dot + word (never colour alone). */
export function SeverityBadge({ severity, className }: { severity: Severity; className?: string }) {
  const { t } = useTranslation();
  return (
    <span className={cn("inline-flex items-center gap-2 text-[13px] font-medium", className)}>
      <span className={cn("size-2 rounded-full", RISK_CLASSES[severity].dot)} aria-hidden="true" />
      {t(`severity.${severity}`)}
    </span>
  );
}
