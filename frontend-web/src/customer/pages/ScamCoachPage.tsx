import { useMutation } from "@tanstack/react-query";
import { Loader2, MessageCircleQuestion, Phone, ShieldCheck, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate, useParams } from "react-router-dom";

import { CountdownTimer } from "@/customer/components/CountdownTimer";
import { YesNoQuestion } from "@/customer/components/YesNoQuestion";
import { usePaymentResult } from "@/customer/hooks/usePaymentResult";
import { BANK_PHONE, COOLING_OFF_MS, flaggedKeys, isCoachKey } from "@/customer/lib/coach";
import { Summary } from "@/customer/pages/RiskWarningPage";
import { type PaymentResult, rememberPayment } from "@/customer/state/payments";
import { toApiError } from "@/shared/api/client";
import { answerCoach } from "@/shared/api/endpoints/transactions";
import { Button } from "@/shared/components/ui/button";
import { buttonVariants } from "@/shared/components/ui/buttonVariants";
import { useNow } from "@/shared/hooks/useNow";
import { useToast } from "@/shared/hooks/useToast";
import { cn } from "@/shared/lib/cn";
import type { CoachQuestion } from "@/shared/types/api";

function useQuestionText() {
  const { t } = useTranslation();
  return (q: CoachQuestion) => (isCoachKey(q.key) ? t(`customer.coach.q.${q.key}`) : q.text);
}

/**
 * Spec 07 §3 Scam Coach (spec 01 F1, T5.4): 2-3 yes/no questions before a risky payment, a
 * 10-minute cooling-off timer and "Call my bank". Any warning-sign answer stops the payment (red
 * screen with advice for those answers); safe answers still wait for the timer. "Cancel payment"
 * is always the biggest button and nothing here closes by itself (spec 07 §4).
 */
export function ScamCoachPage() {
  const { t } = useTranslation();
  const { txnId = "" } = useParams();
  const navigate = useNavigate();
  const { toast } = useToast();
  const questionText = useQuestionText();
  const { result, loading } = usePaymentResult(txnId);
  const [answers, setAnswers] = useState<Record<string, boolean>>({});
  const [passed, setPassed] = useState<PaymentResult | null>(null);
  const [openedAt] = useState(() => Date.now());
  const startedAt = result?.coachStartedAt ?? openedAt;
  const now = useNow(1_000);
  const coolingOver = now >= startedAt + COOLING_OFF_MS;

  // Remember when the coach opened, so a reload doesn't restart the cooling-off time.
  useEffect(() => {
    if (result && result.coachStartedAt === undefined) rememberPayment({ ...result, coachStartedAt: openedAt });
  }, [result, openedAt]);

  const questions = result?.coachQuestions ?? [];
  const submit = useMutation({
    mutationFn: () => answerCoach(txnId, { answers }),
    onSuccess: (res) => {
      if (!result) return;
      const next: PaymentResult = {
        ...result,
        coachStartedAt: startedAt,
        decision: res.decision,
        action: res.action,
        reasons: res.top_reasons,
        explanation: res.explanation,
        explanationPending: res.explanation_status === "pending",
        flagged: flaggedKeys(questions, answers),
      };
      rememberPayment(next);
      if (res.decision === "HOLD") navigate(`/app/pay/${encodeURIComponent(txnId)}/warning`, { replace: true, state: next });
      else if (res.decision === "APPROVE") navigate(`/app/pay/${encodeURIComponent(txnId)}/done`, { replace: true, state: next });
      else setPassed(next);
    },
    onError: (error) => {
      const err = toApiError(error);
      toast({ tone: "error", title: t(err.code === "COACH_ALREADY_ANSWERED" ? "customer.coach.already" : "customer.coach.error"), body: err.message });
    },
  });

  if (loading) {
    return (
      <div className="grid min-h-[60vh] place-items-center" aria-busy="true">
        <Loader2 className="size-8 animate-spin text-muted-foreground" aria-label={t("common.loading")} />
      </div>
    );
  }
  // Nothing to ask (already answered, reloaded elsewhere, not a coach payment): the warning screen.
  if (!result || (!passed && (result.action !== "SCAM_COACH" || questions.length === 0))) {
    return <Navigate to={`/app/pay/${encodeURIComponent(txnId)}/warning`} replace state={result ?? undefined} />;
  }

  const allAnswered = questions.every((q) => answers[q.key] !== undefined);
  const cancel = () => {
    toast({ tone: "success", title: t("customer.warning.cancelled"), body: t("customer.warning.cancelledBody") });
    navigate("/app", { replace: true });
  };
  const payNow = () => {
    if (!passed) return;
    const next = { ...passed, continued: true };
    rememberPayment(next);
    navigate(`/app/pay/${encodeURIComponent(txnId)}/done`, { replace: true, state: next });
  };

  return (
    <div className="pb-6">
      <header role="alert" className="bg-[oklch(0.55_0.15_60)] px-5 pb-6 pt-6 text-white">
        {passed ? <ShieldCheck className="size-12" aria-hidden="true" /> : <MessageCircleQuestion className="size-12" aria-hidden="true" />}
        <h1 className="mt-3 text-2xl font-bold leading-tight">
          {passed ? t("customer.coach.passedTitle") : t("customer.coach.title", { count: questions.length })}
        </h1>
        <p className="mt-2 text-lg leading-snug text-white/95">{passed ? t("customer.coach.passedBody") : t("customer.coach.body")}</p>
      </header>

      <div className="space-y-5 px-5 pt-5">
        <Summary result={result} />

        {!passed && (
          <section className="space-y-3" aria-label={t("customer.coach.questions")}>
            {questions.map((q, i) => (
              <YesNoQuestion
                key={q.key}
                id={`coach-${q.key}`}
                number={i + 1}
                question={questionText(q)}
                value={answers[q.key] ?? null}
                disabled={submit.isPending}
                onChange={(value) => setAnswers((prev) => ({ ...prev, [q.key]: value }))}
              />
            ))}
          </section>
        )}

        <CountdownTimer
          endsAt={startedAt + COOLING_OFF_MS}
          running={(left) => t("customer.coach.coolingRunning", { left })}
          done={t("customer.coach.coolingDone")}
        />

        <div className="space-y-3 pt-1">
          <Button size="lg" className="h-16 w-full text-xl" onClick={cancel}>
            <X className="size-6" aria-hidden="true" />
            {t("customer.warning.cancel")}
          </Button>
          {passed ? (
            <Button variant="outline" size="lg" className="h-14 w-full text-lg" disabled={!coolingOver} onClick={payNow}>
              {coolingOver ? t("customer.coach.payNow") : t("customer.coach.payAfter")}
            </Button>
          ) : (
            <Button
              variant="outline"
              size="lg"
              className="h-14 w-full text-lg"
              disabled={!allAnswered || submit.isPending}
              onClick={() => submit.mutate()}
            >
              {submit.isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
              {t(submit.isPending ? "customer.coach.checking" : "customer.coach.check")}
            </Button>
          )}
          {!passed && !allAnswered && <p className="text-center text-base text-muted-foreground">{t("customer.coach.answerAll")}</p>}
          <a href={`tel:${BANK_PHONE}`} className={cn(buttonVariants({ variant: "ghost", size: "lg" }), "h-14 w-full text-lg")}>
            <Phone aria-hidden="true" />
            {t("customer.coach.callBank", { number: BANK_PHONE })}
          </a>
        </div>
      </div>
    </div>
  );
}
