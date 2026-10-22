import { useQuery } from "@tanstack/react-query";
import { CircleCheck, Lightbulb, OctagonPause, Send, ShieldCheck, ShieldQuestion } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { SimBanner } from "@/customer/components/SimBanner";
import { listTransactions } from "@/shared/api/endpoints/transactions";
import { Logo } from "@/shared/components/Logo";
import { buttonVariants } from "@/shared/components/ui/buttonVariants";
import { useAuth } from "@/shared/hooks/useAuth";
import { cn } from "@/shared/lib/cn";
import { formatDateTime, formatInr, maskVpa } from "@/shared/lib/format";
import { RISK_CLASSES } from "@/shared/lib/riskColor";
import type { Decision } from "@/shared/types/api";

/** A demo account: the balance is invented (spec 07 §3 "fake balance"). */
const DEMO_BALANCE = 124_560;
const RECENT = 5;
const TIPS = ["customer.home.tip1", "customer.home.tip2", "customer.home.tip3", "customer.home.tip4"] as const;

const STATUS: Record<Decision, { icon: typeof CircleCheck; className: string }> = {
  APPROVE: { icon: CircleCheck, className: RISK_CLASSES.low.text },
  STEP_UP: { icon: ShieldQuestion, className: RISK_CLASSES.medium.text },
  HOLD: { icon: OctagonPause, className: RISK_CLASSES.high.text },
};

function RecentPayments() {
  const { t } = useTranslation();
  const recent = useQuery({
    queryKey: ["my-payments"],
    queryFn: () => listTransactions({ mine: true, page_size: RECENT }),
  });
  if (recent.isError) return <p className="text-base text-muted-foreground">{t("customer.home.recentError")}</p>;
  if (!recent.data) {
    return (
      <div className="space-y-2" aria-busy="true">
        {Array.from({ length: 3 }, (_, i) => <div key={i} className="h-16 animate-pulse rounded-xl bg-muted" />)}
      </div>
    );
  }
  if (recent.data.items.length === 0) return <p className="text-base text-muted-foreground">{t("customer.home.noPayments")}</p>;
  return (
    <ul className="divide-y rounded-2xl border bg-card">
      {recent.data.items.map((p) => {
        const status = p.decision ? STATUS[p.decision] : null;
        const Icon = status?.icon;
        return (
          <li key={p.id} className="flex min-h-16 items-center gap-3 px-4 py-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-base font-medium">{maskVpa(p.payee_vpa)}</p>
              <p className="text-base text-muted-foreground">{formatDateTime(p.ts)}</p>
            </div>
            <div className="text-right">
              <p className="text-base font-semibold tabular-nums">{formatInr(p.amount)}</p>
              {p.decision && Icon && (
                <p className="inline-flex items-center gap-1 text-base">
                  <Icon className={cn("size-4", status.className)} aria-hidden="true" />
                  {t(`customer.status.${p.decision}`)}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** Spec 07 §3 Home: balance, a big Pay button, recent payments and a safety tip. */
export function HomePage() {
  const { t } = useTranslation();
  const { state } = useAuth();
  const firstName = state.status === "authenticated" ? (state.user.name.split(" ")[0] ?? "") : "";
  const tip = TIPS[new Date().getDate() % TIPS.length] ?? TIPS[0];
  return (
    <div className="space-y-5 px-5 pb-6 pt-4">
      <header className="flex items-center justify-between">
        <Logo label={t("app.customerName")} />
      </header>
      <h1 className="text-2xl font-semibold tracking-tight">{t("customer.hello", { name: firstName })}</h1>

      <section className="rounded-2xl bg-primary p-5 text-primary-foreground shadow-sm">
        <p className="text-base text-primary-foreground/85">{t("customer.home.balance")}</p>
        <p className="mt-1 text-3xl font-semibold tabular-nums tracking-tight">{formatInr(DEMO_BALANCE)}</p>
        <p className="mt-3 flex items-center gap-2 text-base text-primary-foreground/90">
          <ShieldCheck className="size-5 shrink-0" aria-hidden="true" />
          {t("customer.protectedBody")}
        </p>
      </section>

      <Link to="/app/pay" className={cn(buttonVariants({ size: "lg" }), "h-14 w-full text-lg")}>
        <Send aria-hidden="true" />
        {t("customer.home.pay")}
      </Link>

      <SimBanner />

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">{t("customer.home.recent")}</h2>
        <RecentPayments />
      </section>

      <section className="flex gap-3 rounded-2xl border border-risk-medium/40 bg-risk-medium/10 p-4">
        <Lightbulb className="mt-0.5 size-6 shrink-0 text-risk-medium" aria-hidden="true" />
        <div>
          <h2 className="text-base font-semibold">{t("customer.home.tipTitle")}</h2>
          <p className="mt-1 text-base leading-relaxed">{t(tip)}</p>
        </div>
      </section>
    </div>
  );
}
