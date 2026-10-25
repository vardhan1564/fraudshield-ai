import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, Send } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { ReputationChip } from "@/customer/components/ReputationChip";
import { SimBanner } from "@/customer/components/SimBanner";
import { behaviour, deviceId, newTxnId, usualPhoneId } from "@/customer/lib/device";
import { fromScore, rememberPayment } from "@/customer/state/payments";
import { toPaymentContext } from "@/customer/state/simContext";
import { useSimContext } from "@/customer/state/useSimContext";
import { toApiError } from "@/shared/api/client";
import { payeeReputation } from "@/shared/api/endpoints/payees";
import { scorePayment } from "@/shared/api/endpoints/transactions";
import { Button } from "@/shared/components/ui/button";
import { useAuth } from "@/shared/hooks/useAuth";
import { cn } from "@/shared/lib/cn";
import { formatInr } from "@/shared/lib/format";

const VPA = /^[a-z0-9][a-z0-9._-]{1,63}@[a-z][a-z0-9.-]{1,63}$/;
export const MAX_UPI = 100_000; // NPCI's usual per-payment UPI limit
const QUICK = [200, 500, 2_000] as const;

function cleanVpa(raw: string): string {
  return raw.trim().toLowerCase();
}

/**
 * Spec 07 §3 Pay: payee UPI ID (its reputation shows as soon as the field is left), amount, Pay.
 * The payment is scored with the simulated phone context; a safe one goes straight to Done, a
 * risky one to the Risk Warning.
 */
export function PayPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { state } = useAuth();
  const { sim } = useSimContext();
  const [vpa, setVpa] = useState("");
  const [amount, setAmount] = useState("");
  const [checked, setChecked] = useState<string | null>(null); // the VPA whose reputation is shown
  const [errors, setErrors] = useState<{ vpa?: string; amount?: string }>({});

  const reputation = useQuery({
    queryKey: ["reputation", checked],
    queryFn: () => payeeReputation(checked ?? ""),
    enabled: checked !== null,
    staleTime: 60_000,
  });

  const pay = useMutation({
    mutationFn: async ({ payee, rupees }: { payee: string; rupees: number }) => {
      const at = new Date().toISOString();
      const userId = state.status === "authenticated" ? state.user.id : "";
      const response = await scorePayment({
        txn_id: newTxnId(),
        user_id: userId,
        amount: rupees,
        channel: "UPI",
        payee_vpa: payee,
        merchant_category: "P2P",
        device_id: sim.usualPhone ? usualPhoneId(userId) : deviceId(),
        timestamp: at,
        context: toPaymentContext(sim),
        behaviour: behaviour(),
      });
      return fromScore(response, payee, rupees, at);
    },
    onSuccess: (result) => {
      rememberPayment(result);
      const next = result.decision === "APPROVE" ? "done" : result.action === "SCAM_COACH" ? "coach" : "warning";
      navigate(`/app/pay/${encodeURIComponent(result.txnId)}/${next}`, { state: result });
    },
  });

  const vpaError = (value: string) => (VPA.test(cleanVpa(value)) ? undefined : t("customer.pay.vpaInvalid"));
  const amountError = (value: string) => {
    const rupees = Number(value);
    if (!value || !Number.isFinite(rupees) || rupees <= 0) return t("customer.pay.amountInvalid");
    if (rupees > MAX_UPI) return t("customer.pay.amountTooBig", { max: formatInr(MAX_UPI) });
    if (Math.round(rupees * 100) !== rupees * 100) return t("customer.pay.amountInvalid");
    return undefined;
  };

  const onVpaBlur = () => {
    const error = vpa ? vpaError(vpa) : undefined;
    setErrors((e) => ({ ...e, vpa: error }));
    if (!error && vpa) setChecked(cleanVpa(vpa));
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const next = { vpa: vpaError(vpa), amount: amountError(amount) };
    setErrors(next);
    if (next.vpa || next.amount) return;
    setChecked(cleanVpa(vpa));
    pay.mutate({ payee: cleanVpa(vpa), rupees: Number(amount) });
  };

  const field = "mt-1.5 h-14 w-full rounded-xl border bg-card px-4 text-lg outline-none focus-visible:ring-3 focus-visible:ring-ring/25";

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-5 px-5 pb-6 pt-4">
      <h1 className="text-2xl font-semibold tracking-tight">{t("customer.pay.title")}</h1>
      <SimBanner />

      <div>
        <label htmlFor="pay-vpa" className="text-base font-medium">{t("customer.pay.to")}</label>
        <input
          id="pay-vpa"
          value={vpa}
          onChange={(e) => {
            setVpa(e.target.value);
            if (checked && cleanVpa(e.target.value) !== checked) setChecked(null);
          }}
          onBlur={onVpaBlur}
          inputMode="email"
          autoCapitalize="none"
          autoComplete="off"
          spellCheck={false}
          placeholder={t("customer.pay.toPlaceholder")}
          aria-invalid={errors.vpa ? true : undefined}
          aria-describedby="pay-vpa-help"
          className={cn(field, errors.vpa && "border-destructive")}
        />
        <div id="pay-vpa-help" className="mt-2">
          {errors.vpa ? (
            <p className="text-base text-destructive">{errors.vpa}</p>
          ) : (
            <ReputationChip reputation={checked ? reputation.data : undefined} loading={checked !== null && reputation.isPending} />
          )}
        </div>
      </div>

      <div>
        <label htmlFor="pay-amount" className="text-base font-medium">{t("customer.pay.amount")}</label>
        <div className="relative">
          <span className="pointer-events-none absolute left-4 top-1/2 mt-[3px] -translate-y-1/2 text-lg text-muted-foreground" aria-hidden="true">₹</span>
          <input
            id="pay-amount"
            value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))}
            inputMode="decimal"
            autoComplete="off"
            placeholder="0"
            aria-invalid={errors.amount ? true : undefined}
            aria-describedby={errors.amount ? "pay-amount-error" : undefined}
            className={cn(field, "pl-9 tabular-nums", errors.amount && "border-destructive")}
          />
        </div>
        {errors.amount && <p id="pay-amount-error" className="mt-2 text-base text-destructive">{errors.amount}</p>}
        <div className="mt-3 flex gap-2">
          {QUICK.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => { setAmount(String(q)); setErrors((e) => ({ ...e, amount: undefined })); }}
              className="min-h-11 flex-1 cursor-pointer rounded-xl border bg-card text-base font-medium hover:bg-muted"
            >
              {formatInr(q)}
            </button>
          ))}
        </div>
      </div>

      {pay.isError && (
        <p role="alert" className="rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-base">
          {t("customer.pay.failed", { message: toApiError(pay.error).message })}
        </p>
      )}

      <Button type="submit" size="lg" className="h-14 w-full text-lg" disabled={pay.isPending}>
        {pay.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Send aria-hidden="true" />}
        {pay.isPending ? t("customer.pay.checking") : t("customer.pay.submit")}
      </Button>
      <p className="text-center text-base text-muted-foreground">{t("customer.pay.note")}</p>
    </form>
  );
}
