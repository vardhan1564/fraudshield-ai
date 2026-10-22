import { CircleCheck, Loader2, SearchX, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, useParams } from "react-router-dom";

import { usePaymentResult } from "@/customer/hooks/usePaymentResult";
import { EmptyState } from "@/shared/components/EmptyState";
import { buttonVariants } from "@/shared/components/ui/buttonVariants";
import { cn } from "@/shared/lib/cn";
import { formatDateTime, formatInr, maskVpa } from "@/shared/lib/format";

/** The success screen (spec 07 §5 "Normal payment": success in < 1 s). */
export function PaymentDonePage() {
  const { t } = useTranslation();
  const { txnId = "" } = useParams();
  const { result, loading } = usePaymentResult(txnId);

  if (loading) {
    return (
      <div className="grid min-h-[60vh] place-items-center" aria-busy="true">
        <Loader2 className="size-8 animate-spin text-muted-foreground" aria-label={t("common.loading")} />
      </div>
    );
  }
  if (!result) {
    return (
      <div className="px-5 py-8">
        <EmptyState icon={SearchX} title={t("customer.warning.notFound")} />
      </div>
    );
  }
  // A risky payment never shows "sent" unless the customer chose to go ahead.
  if (result.decision === "HOLD" || (result.decision === "STEP_UP" && !result.continued)) {
    return <Navigate to={`/app/pay/${encodeURIComponent(txnId)}/warning`} replace state={result} />;
  }

  return (
    <div className="flex min-h-full flex-col px-5 pb-6 pt-10 text-center">
      <CircleCheck className="mx-auto size-20 text-risk-low" aria-hidden="true" />
      <div role="status">
        <h1 className="mt-4 text-2xl font-semibold">{t("customer.done.title")}</h1>
      </div>
      <p className="mt-2 text-4xl font-semibold tabular-nums tracking-tight">{formatInr(result.amount)}</p>
      <p className="mt-1 text-lg">{t("customer.warning.to", { payee: maskVpa(result.payee) })}</p>

      <dl className="mx-auto mt-6 w-full space-y-2 rounded-2xl border bg-card p-4 text-left text-base">
        <div className="flex justify-between gap-3">
          <dt className="text-muted-foreground">{t("customer.done.when")}</dt>
          <dd>{formatDateTime(result.at)}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-muted-foreground">{t("customer.done.ref")}</dt>
          <dd className="break-all font-mono text-[15px]">{result.txnId}</dd>
        </div>
      </dl>

      {result.continued && (
        <p className="mt-4 flex items-start gap-2 rounded-xl border border-risk-medium/40 bg-risk-medium/10 p-3 text-left text-base">
          <TriangleAlert className="mt-0.5 size-5 shrink-0 text-risk-medium" aria-hidden="true" />
          {t("customer.done.afterWarning")}
        </p>
      )}

      <div className="mt-auto space-y-3 pt-8">
        <Link to="/app" replace className={cn(buttonVariants({ size: "lg" }), "h-14 w-full text-lg")}>
          {t("customer.done.home")}
        </Link>
        <Link to="/app/pay" replace className={cn(buttonVariants({ variant: "outline", size: "lg" }), "h-14 w-full text-lg")}>
          {t("customer.done.another")}
        </Link>
      </div>
    </div>
  );
}
