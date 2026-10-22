import { Info, Loader2, ShieldAlert, ShieldCheck, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/shared/lib/cn";
import type { PayeeReputation } from "@/shared/types/api";

/** Reported by this many people = the PRD §8 "≥ 5 reports" warning line. */
export const REPORTED_FROM = 5;
const NEW_ACCOUNT_DAYS = 30;

type Look = { icon: typeof Info; tone: string; text: string };

/**
 * What other customers know about this payee, shown before paying (spec 07 §3, §5 "Reported
 * payee"). Icon + words on a tinted strip; text stays in the normal ink for contrast.
 */
export function ReputationChip({ reputation, loading }: { reputation: PayeeReputation | undefined; loading: boolean }) {
  const { t } = useTranslation();
  if (loading) {
    return (
      <p className="flex min-h-11 items-center gap-2.5 text-base text-muted-foreground" role="status">
        <Loader2 className="size-5 animate-spin" aria-hidden="true" />
        {t("customer.rep.checking")}
      </p>
    );
  }
  if (!reputation) return null;

  const r = reputation;
  let look: Look;
  if (r.risk_level === "high" || r.report_count >= REPORTED_FROM || r.confirmed_fraud_count > 0) {
    look = { icon: ShieldAlert, tone: "border-risk-high/40 bg-risk-high/10 [&_svg]:text-risk-high",
             text: t("customer.rep.reported", { count: Math.max(r.report_count, r.confirmed_fraud_count) }) };
  } else if (r.report_count > 0 || r.risk_level === "medium") {
    look = { icon: TriangleAlert, tone: "border-risk-medium/50 bg-risk-medium/12 [&_svg]:text-risk-medium",
             text: t("customer.rep.someReports", { count: r.report_count }) };
  } else if (!r.known) {
    look = { icon: Info, tone: "border-border bg-muted [&_svg]:text-muted-foreground", text: t("customer.rep.unknown") };
  } else if (r.account_age_days !== null && r.account_age_days < NEW_ACCOUNT_DAYS) {
    look = { icon: TriangleAlert, tone: "border-risk-medium/50 bg-risk-medium/12 [&_svg]:text-risk-medium",
             text: t("customer.rep.newAccount", { count: r.account_age_days }) };
  } else {
    look = { icon: ShieldCheck, tone: "border-risk-low/40 bg-risk-low/10 [&_svg]:text-risk-low", text: t("customer.rep.clean") };
  }
  const Icon = look.icon;
  return (
    <p role="status" className={cn("flex min-h-11 items-center gap-2.5 rounded-xl border px-3.5 py-2.5 text-base font-medium", look.tone)}>
      <Icon className="size-5 shrink-0" aria-hidden="true" />
      {look.text}
    </p>
  );
}
