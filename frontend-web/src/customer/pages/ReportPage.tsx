import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Briefcase,
  CircleCheck,
  CircleHelp,
  FileText,
  Flag,
  Gift,
  Headset,
  Info,
  Link2,
  Loader2,
  Siren,
  TrendingUp,
} from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";

import { ApiError, toApiError } from "@/shared/api/client";
import { reportPayee } from "@/shared/api/endpoints/payees";
import { Button } from "@/shared/components/ui/button";
import { buttonVariants } from "@/shared/components/ui/buttonVariants";
import { cn } from "@/shared/lib/cn";
import { maskVpa } from "@/shared/lib/format";
import type { ReportCreated, ScamType } from "@/shared/types/api";

const VPA = /^[a-z0-9][a-z0-9._-]{1,63}@[a-z][a-z0-9.-]{1,63}$/;
const MOBILE = /^[6-9]\d{9}$/; // as the API: +91 / 0 prefixes and spaces are dropped first
const DESCRIPTION_MAX = 2000;

const TYPES: { value: ScamType; icon: typeof Flag }[] = [
  { value: "digital_arrest", icon: Siren },
  { value: "fake_support_refund", icon: Headset },
  { value: "investment_task", icon: TrendingUp },
  { value: "fake_kyc", icon: FileText },
  { value: "lottery_prize", icon: Gift },
  { value: "job_task", icon: Briefcase },
  { value: "phishing", icon: Link2 },
  { value: "other", icon: CircleHelp },
];

function cleanPhone(raw: string): string {
  return raw.replace(/[\s-]/g, "").replace(/^\+91/, "").replace(/^0/, "");
}

/** The thank-you screen: how many people have now reported this payee. */
function Reported({ result, onAnother }: { result: ReportCreated; onAnother: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-full flex-col px-5 pb-6 pt-10 text-center">
      <CircleCheck className="mx-auto size-20 text-risk-low" aria-hidden="true" />
      <div role="status">
        <h1 className="mt-4 text-2xl font-semibold">{t("customer.report.thanks")}</h1>
        <p className="mt-3 text-lg leading-snug">
          {t("customer.report.count", { count: result.report_count, payee: maskVpa(result.payee_vpa) })}
        </p>
      </div>
      <p className="mt-2 text-base text-muted-foreground">{t("customer.report.warnOthers")}</p>
      <div className="mt-auto space-y-3 pt-8">
        <Link to="/app" replace className={cn(buttonVariants({ size: "lg" }), "h-14 w-full text-lg")}>
          {t("customer.done.home")}
        </Link>
        <Button variant="outline" size="lg" className="h-14 w-full text-lg" onClick={onAnother}>
          {t("customer.report.another")}
        </Button>
      </div>
    </div>
  );
}

/**
 * Spec 07 §3 Report Fraud: tell us about a scam payee (and the number that called) so the next
 * person is warned before paying (spec 01 F7). The Risk Warning's "Report this payee" opens it
 * with the UPI ID filled in, so reporting is one tap plus the kind of scam.
 */
export function ReportPage() {
  const { t } = useTranslation();
  const location = useLocation();
  const queryClient = useQueryClient();
  const prefill = (location.state as { vpa?: string } | null)?.vpa ?? "";
  const [vpa, setVpa] = useState(prefill);
  const [scamType, setScamType] = useState<ScamType | null>(null);
  const [phone, setPhone] = useState("");
  const [description, setDescription] = useState("");
  const [errors, setErrors] = useState<{ vpa?: string; scamType?: string; phone?: string }>({});

  const report = useMutation({
    mutationFn: reportPayee,
    onSuccess: (result) => {
      // the Pay screen's reputation chip shows the new count straight away
      void queryClient.invalidateQueries({ queryKey: ["reputation", result.payee_vpa] });
    },
  });

  const already = report.error instanceof ApiError && report.error.code === "ALREADY_REPORTED";

  const reset = () => {
    report.reset();
    setVpa("");
    setScamType(null);
    setPhone("");
    setDescription("");
    setErrors({});
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const payee = vpa.trim().toLowerCase();
    const number = cleanPhone(phone);
    const next = {
      vpa: VPA.test(payee) ? undefined : t("customer.pay.vpaInvalid"),
      scamType: scamType ? undefined : t("customer.report.typeNeeded"),
      phone: !number || MOBILE.test(number) ? undefined : t("customer.report.phoneInvalid"),
    };
    setErrors(next);
    if (next.vpa || next.scamType || next.phone || !scamType) return;
    report.mutate({
      payee_vpa: payee,
      scam_type: scamType,
      phone: number || null,
      description: description.trim() || null,
    });
  };

  if (report.data) return <Reported result={report.data} onAnother={reset} />;

  const field = "mt-1.5 w-full rounded-xl border bg-card px-4 text-lg outline-none focus-visible:ring-3 focus-visible:ring-ring/25";

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-5 px-5 pb-6 pt-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("customer.report.title")}</h1>
        <p className="mt-1 text-base text-muted-foreground">{t("customer.report.intro")}</p>
      </div>

      <div>
        <label htmlFor="report-vpa" className="text-base font-medium">{t("customer.report.vpa")}</label>
        <input
          id="report-vpa"
          value={vpa}
          onChange={(e) => setVpa(e.target.value)}
          inputMode="email"
          autoCapitalize="none"
          autoComplete="off"
          spellCheck={false}
          placeholder={t("customer.pay.toPlaceholder")}
          aria-invalid={errors.vpa ? true : undefined}
          aria-describedby={errors.vpa ? "report-vpa-error" : undefined}
          className={cn(field, "h-14", errors.vpa && "border-destructive")}
        />
        {errors.vpa && <p id="report-vpa-error" className="mt-2 text-base text-destructive">{errors.vpa}</p>}
      </div>

      <fieldset aria-describedby={errors.scamType ? "report-type-error" : undefined}>
        <legend className="text-base font-medium">{t("customer.report.type")}</legend>
        <div role="radiogroup" aria-label={t("customer.report.type")} className="mt-2 grid grid-cols-2 gap-2">
          {TYPES.map(({ value, icon: Icon }) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={scamType === value}
              onClick={() => { setScamType(value); setErrors((er) => ({ ...er, scamType: undefined })); }}
              className={cn(
                "flex min-h-16 cursor-pointer items-center gap-2.5 rounded-xl border bg-card px-3 py-2 text-left text-base font-medium leading-snug",
                scamType === value ? "border-primary bg-secondary ring-2 ring-primary/30" : "hover:bg-muted",
              )}
            >
              <Icon className="size-5 shrink-0 text-primary" aria-hidden="true" />
              {t(`customer.report.types.${value}`)}
            </button>
          ))}
        </div>
        {errors.scamType && <p id="report-type-error" className="mt-2 text-base text-destructive">{errors.scamType}</p>}
      </fieldset>

      <div>
        <label htmlFor="report-phone" className="text-base font-medium">{t("customer.report.phone")}</label>
        <input
          id="report-phone"
          value={phone}
          onChange={(e) => setPhone(e.target.value.replace(/[^\d+\s-]/g, ""))}
          inputMode="tel"
          autoComplete="off"
          placeholder="98xxxxxx21"
          aria-invalid={errors.phone ? true : undefined}
          aria-describedby={errors.phone ? "report-phone-error" : undefined}
          className={cn(field, "h-14 tabular-nums", errors.phone && "border-destructive")}
        />
        {errors.phone && <p id="report-phone-error" className="mt-2 text-base text-destructive">{errors.phone}</p>}
      </div>

      <div>
        <label htmlFor="report-what" className="text-base font-medium">{t("customer.report.what")}</label>
        <textarea
          id="report-what"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          maxLength={DESCRIPTION_MAX}
          rows={3}
          placeholder={t("customer.report.whatPlaceholder")}
          className={cn(field, "resize-y py-3")}
        />
      </div>

      {already && (
        <p role="status" className="flex items-start gap-2.5 rounded-xl border bg-muted px-4 py-3 text-base">
          <Info className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" />
          <span>
            {t("customer.report.already")}{" "}
            <Link to="/app" className="font-medium text-primary underline underline-offset-4">{t("customer.done.home")}</Link>
          </span>
        </p>
      )}
      {report.isError && !already && (
        <p role="alert" className="rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-base">
          {t("customer.report.failed", { message: toApiError(report.error).message })}
        </p>
      )}

      <Button type="submit" size="lg" className="h-14 w-full text-lg" disabled={report.isPending}>
        {report.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Flag aria-hidden="true" />}
        {report.isPending ? t("customer.report.sending") : t("customer.report.submit")}
      </Button>
    </form>
  );
}
