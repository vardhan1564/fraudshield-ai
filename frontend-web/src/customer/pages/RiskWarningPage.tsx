import { Flag, Loader2, OctagonPause, Phone, SearchX, TriangleAlert, X } from "lucide-react";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import { useCustomerSocket } from "@/customer/hooks/useCustomerSocket";
import { usePaymentResult } from "@/customer/hooks/usePaymentResult";
import { BANK_PHONE, HELPLINE, isCoachKey } from "@/customer/lib/coach";
import { type PaymentResult, rememberPayment } from "@/customer/state/payments";
import { EmptyState } from "@/shared/components/EmptyState";
import { ReasonList } from "@/shared/components/ReasonList";
import { Button } from "@/shared/components/ui/button";
import { buttonVariants } from "@/shared/components/ui/buttonVariants";
import { useToast } from "@/shared/hooks/useToast";
import { cn } from "@/shared/lib/cn";
import { formatInr, maskVpa } from "@/shared/lib/format";
import type { CustomerMessage } from "@/shared/types/api";


export function Summary({ result }: { result: PaymentResult }) {
  const { t } = useTranslation();
  return (
    <div className="rounded-2xl border bg-card p-4">
      <p className="text-base text-muted-foreground">{t("customer.warning.youArePaying")}</p>
      <p className="mt-0.5 text-2xl font-semibold tabular-nums">{formatInr(result.amount)}</p>
      <p className="text-base">{t("customer.warning.to", { payee: maskVpa(result.payee) })}</p>
    </div>
  );
}

/**
 * Spec 07 §3 Risk Warning: an extra check (amber) or a paused payment (red). Reasons and the
 * plain-language explanation, never a score; it never closes by itself and "Cancel payment" is
 * always the biggest button (spec 07 §4). The worker's better sentence replaces the first one live.
 */
export function RiskWarningPage() {
  const { t } = useTranslation();
  const { txnId = "" } = useParams();
  const navigate = useNavigate();
  const { toast } = useToast();
  const { result, loading } = usePaymentResult(txnId);
  const [live, setLive] = useState<string | null>(null);

  const onMessage = useCallback(
    (message: CustomerMessage) => {
      if (message.type !== "explanation.ready" || message.data.txn_id !== txnId) return;
      setLive(message.data.explanation);
      if (result) rememberPayment({ ...result, explanation: message.data.explanation, explanationPending: false });
    },
    [txnId, result],
  );
  useCustomerSocket(onMessage);

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
        <EmptyState
          icon={SearchX}
          title={t("customer.warning.notFound")}
          action={<Link to="/app" className={cn(buttonVariants({ size: "lg" }), "text-base")}>{t("customer.done.home")}</Link>}
        />
      </div>
    );
  }
  if (result.decision === "APPROVE") return <Navigate to={`/app/pay/${encodeURIComponent(txnId)}/done`} replace state={result} />;

  const held = result.decision === "HOLD";
  // stopped by the customer's own Scam Coach answers: say which answers worried us, and why
  const flagged = held ? (result.flagged ?? []).filter(isCoachKey) : [];
  const explanation = live ?? result.explanation;
  const cancel = () => {
    toast({ tone: "success", title: t("customer.warning.cancelled"), body: t("customer.warning.cancelledBody") });
    navigate("/app", { replace: true });
  };
  const goAhead = () => {
    const next = { ...result, continued: true };
    rememberPayment(next);
    navigate(`/app/pay/${encodeURIComponent(txnId)}/done`, { replace: true, state: next });
  };

  return (
    <div className="pb-6">
      <header
        role="alert"
        className={cn(
          "px-5 pb-6 pt-6 text-white",
          held ? "bg-[oklch(0.5_0.2_27)]" : "bg-[oklch(0.55_0.15_60)]",
        )}
      >
        {held ? <OctagonPause className="size-12" aria-hidden="true" /> : <TriangleAlert className="size-12" aria-hidden="true" />}
        <h1 className="mt-3 text-2xl font-bold leading-tight">
          {t(flagged.length ? "customer.coach.heldTitle" : held ? "customer.warning.heldTitle" : "customer.warning.checkTitle")}
        </h1>
        <p className="mt-2 text-lg leading-snug text-white/95">
          {t(flagged.length ? "customer.coach.heldBody" : held ? "customer.warning.heldBody" : "customer.warning.checkBody")}
        </p>
      </header>

      <div className="space-y-5 px-5 pt-5">
        <Summary result={result} />

        {flagged.length > 0 && (
          <section className="space-y-3">
            <h2 className="text-lg font-semibold">{t("customer.coach.tipsTitle")}</h2>
            <ul className="space-y-2">
              {flagged.map((key) => (
                <li key={key} className="rounded-2xl border-2 border-destructive/40 bg-destructive/5 px-4 py-3 text-lg leading-snug">
                  {t(`customer.coach.tips.${key}`)}
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="space-y-3">
          <h2 className="text-lg font-semibold">{t("customer.warning.why")}</h2>
          <p aria-live="polite" className="rounded-2xl bg-muted px-4 py-3 text-lg leading-relaxed">
            {explanation}
            {result.explanationPending && live === null && (
              <span className="mt-2 flex items-center gap-2 text-base text-muted-foreground">
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                {t("customer.warning.moreComing")}
              </span>
            )}
          </p>
          {result.reasons.length > 0 && <ReasonList reasons={result.reasons} />}
        </section>

        <div className="space-y-3 pt-1">
          <Button size="lg" className="h-16 w-full text-xl" onClick={cancel}>
            <X className="size-6" aria-hidden="true" />
            {t("customer.warning.cancel")}
          </Button>
          {held ? (
            <>
              <a href={`tel:${BANK_PHONE}`} className={cn(buttonVariants({ variant: "outline", size: "lg" }), "h-14 w-full text-lg")}>
                <Phone aria-hidden="true" />
                {t("customer.coach.callBank", { number: BANK_PHONE })}
              </a>
              {BANK_PHONE !== HELPLINE && (
                <a href={`tel:${HELPLINE}`} className="flex min-h-11 items-center justify-center gap-2 text-base font-medium text-primary underline underline-offset-4">
                  {t("customer.warning.callHelpline", { number: HELPLINE })}
                </a>
              )}
            </>
          ) : (
            <button
              type="button"
              onClick={goAhead}
              className="min-h-11 w-full cursor-pointer text-base text-muted-foreground underline underline-offset-4 hover:text-foreground"
            >
              {t("customer.warning.continue")}
            </button>
          )}
        </div>
        {held && <p className="text-base text-muted-foreground">{t("customer.warning.heldNext")}</p>}
        <Link
          to="/app/report"
          state={{ vpa: result.payee }}
          className="flex min-h-11 items-center justify-center gap-2 text-base font-medium text-primary underline underline-offset-4"
        >
          <Flag className="size-5" aria-hidden="true" />
          {t("customer.warning.report")}
        </Link>
      </div>
    </div>
  );
}
