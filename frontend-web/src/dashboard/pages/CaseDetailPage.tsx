import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUpRight,
  CircleCheck,
  FileQuestion,
  Megaphone,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { type ReactNode, useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { SeverityBadge } from "@/dashboard/components/SeverityBadge";
import { ShapWaterfall } from "@/dashboard/components/ShapWaterfall";
import { caseKey, QUEUE_KEY, useAlertStream } from "@/dashboard/hooks/useAlertStream";
import { ApiError, toApiError } from "@/shared/api/client";
import { getCase, updateCase } from "@/shared/api/endpoints/cases";
import { explainTransaction, getTransaction, listTransactions } from "@/shared/api/endpoints/transactions";
import { DecisionBadge } from "@/shared/components/DecisionBadge";
import { EmptyState } from "@/shared/components/EmptyState";
import { ErrorState } from "@/shared/components/ErrorState";
import { RiskBadge } from "@/shared/components/RiskBadge";
import { Badge } from "@/shared/components/ui/badge";
import { Button } from "@/shared/components/ui/button";
import { buttonVariants } from "@/shared/components/ui/buttonVariants";
import { Card } from "@/shared/components/ui/card";
import { useAuth } from "@/shared/hooks/useAuth";
import { useToast } from "@/shared/hooks/useToast";
import { cn } from "@/shared/lib/cn";
import { formatDateTime, formatInr, maskVpa } from "@/shared/lib/format";
import type { AlertItem, CaseDetail, CaseUpdate, Language, Page, TxnDetail } from "@/shared/types/api";

const HISTORY_SIZE = 10;

type Verdict = "fraud" | "legit" | "escalate";

/** The PATCH each button sends (spec 04 §2.4). Escalate = severity high, back in the queue. */
function bodyFor(verdict: Verdict, note: string | undefined): CaseUpdate {
  const notes = note ? { notes: note } : {};
  if (verdict === "escalate") return { severity: "high", status: "open", assigned_to: null, ...notes };
  return { label: verdict, status: "closed", ...notes };
}

function Section({ title, action, children, className }: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Card className={cn("p-5", className)}>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {action}
      </div>
      {children}
    </Card>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 break-words text-sm font-medium">{children}</dd>
    </div>
  );
}

function PaymentCard({ c }: { c: CaseDetail }) {
  const { t } = useTranslation();
  const txn = c.transaction;
  const customer = txn.customer;
  return (
    <Section title={t("case.payment.title")}>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-3xl font-semibold tabular-nums tracking-tight">{formatInr(txn.amount)}</p>
          <p className="mt-1 text-sm text-muted-foreground">
            {t("case.payment.to", { payee: maskVpa(txn.payee_vpa) })}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {txn.risk_score != null && <RiskBadge score={txn.risk_score} />}
          {txn.decision && <DecisionBadge decision={txn.decision} />}
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 border-t pt-4 sm:grid-cols-3">
        <Field label={t("case.payment.customer")}>
          {customer.name}
          {customer.external_id && <span className="ml-1.5 font-normal text-muted-foreground">{customer.external_id}</span>}
        </Field>
        <Field label={t("case.payment.phone")}>{customer.phone ?? "—"}</Field>
        <Field label={t("case.payment.time")}>{formatDateTime(txn.ts)}</Field>
        <Field label={t("case.payment.channel")}>{t(`channels.${txn.channel}`)}</Field>
        <Field label={t("case.payment.category")}>{txn.merchant_category ?? "—"}</Field>
        <Field label={t("case.payment.action")}>{txn.action ?? "—"}</Field>
        <Field label={t("case.payment.txnId")}>
          <span className="font-mono text-[13px]">{txn.txn_id}</span>
        </Field>
        <Field label={t("case.payment.model")}>
          <span className="font-mono text-[13px]">{txn.model_version ?? "—"}</span>
        </Field>
      </dl>
    </Section>
  );
}

function ExplanationCard({ text, live, reasons }: { text: string | null; live: boolean; reasons: CaseDetail["top_reasons"] }) {
  const { t } = useTranslation();
  return (
    <Section
      title={t("case.explanation.title")}
      action={
        live && (
          <Badge>
            <Sparkles className="size-3" aria-hidden="true" />
            {t("case.explanation.updated")}
          </Badge>
        )
      }
    >
      <p
        aria-live="polite"
        className={cn("rounded-lg px-3.5 py-3 text-[15px] leading-relaxed", live ? "animate-row-in bg-secondary" : "bg-muted/60")}
      >
        {text ?? <span className="text-muted-foreground">{t("case.explanation.pending")}</span>}
      </p>
      {reasons.length > 0 && (
        <>
          <h3 className="mb-2 mt-4 text-xs font-medium text-muted-foreground">{t("case.explanation.reasons")}</h3>
          <ol className="space-y-1.5 text-sm">
            {reasons.map((r, i) => (
              <li key={`${r.feature}-${i}`} className="flex gap-2.5">
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-risk-high/10 text-[11px] font-semibold text-risk-high">
                  {i + 1}
                </span>
                {r.text}
              </li>
            ))}
          </ol>
        </>
      )}
    </Section>
  );
}

function yesNo(value: unknown, t: (key: "case.shap.yes" | "case.shap.no") => string): string {
  return value ? t("case.shap.yes") : t("case.shap.no");
}

function DeviceCard({ txn, loading }: { txn: TxnDetail | undefined; loading: boolean }) {
  const { t } = useTranslation();
  const ctx = txn?.context ?? {};
  const callS = typeof ctx.call_duration_s === "number" ? ctx.call_duration_s : 0;
  return (
    <Section title={t("case.device.title")}>
      {loading ? (
        <div className="h-40 animate-pulse rounded-lg bg-muted/70" aria-busy="true" />
      ) : (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
          <Field label={t("case.device.fingerprint")}>
            <span className="font-mono text-[13px]">{txn?.device?.fingerprint ?? "—"}</span>
          </Field>
          <Field label={t("case.device.firstSeen")}>{txn?.device ? formatDateTime(txn.device.first_seen) : "—"}</Field>
          <Field label={t("case.device.rooted")}>{txn?.device ? yesNo(txn.device.is_rooted, t) : "—"}</Field>
          <Field label={t("case.device.ip")}>
            <span className="font-mono text-[13px]">{txn?.ip ?? "—"}</span>
          </Field>
          <Field label={t("case.device.location")}>
            {txn?.geo_lat != null && txn.geo_lon != null ? `${txn.geo_lat.toFixed(2)}, ${txn.geo_lon.toFixed(2)}` : "—"}
          </Field>
          <Field label={t("case.device.vpn")}>{yesNo(ctx.is_vpn, t)}</Field>
          <Field label={t("case.device.onCall")}>
            {ctx.on_call
              ? `${t(ctx.caller_in_contacts ? "case.device.knownCaller" : "case.device.unknownCaller")}${callS ? ` · ${Math.round(callS / 60)} min` : ""}`
              : t("case.shap.no")}
          </Field>
          <Field label={t("case.device.screenShare")}>
            {ctx.screen_share_active || ctx.remote_access_app
              ? String(ctx.remote_access_app ?? t("case.shap.yes"))
              : t("case.shap.no")}
          </Field>
        </dl>
      )}
    </Section>
  );
}

function HistoryCard({ customerId, currentTxn }: { customerId: string; currentTxn: string }) {
  const { t } = useTranslation();
  const history = useQuery({
    queryKey: ["customer-history", customerId],
    queryFn: () => listTransactions({ user_id: customerId, page_size: HISTORY_SIZE }),
  });
  return (
    <Section title={t("case.history.title")}>
      {history.isError ? (
        <p className="text-sm text-muted-foreground">{toApiError(history.error).message}</p>
      ) : !history.data ? (
        <div className="h-48 animate-pulse rounded-lg bg-muted/70" aria-busy="true" />
      ) : history.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("case.history.empty")}</p>
      ) : (
        <>
          <ul className="-mx-2 divide-y">
            {history.data.items.map((p) => (
              <li
                key={p.id}
                className={cn("flex items-center gap-3 rounded-md px-2 py-2 text-sm", p.txn_id === currentTxn && "bg-secondary")}
                aria-current={p.txn_id === currentTxn ? "true" : undefined}
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{maskVpa(p.payee_vpa)}</p>
                  <p className="text-xs text-muted-foreground">{formatDateTime(p.ts)}</p>
                </div>
                <div className="text-right">
                  <p className="font-medium tabular-nums">{formatInr(p.amount)}</p>
                  {p.decision && <DecisionBadge decision={p.decision} className="text-xs [&_svg]:size-3.5" />}
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-muted-foreground">
            {t("case.history.count", { shown: history.data.items.length, total: history.data.total })}
          </p>
        </>
      )}
    </Section>
  );
}

function VerdictCard({ c, me, pending, onVerdict }: {
  c: CaseDetail;
  me: string;
  pending: Verdict | null;
  onVerdict: (verdict: Verdict, note: string | undefined) => void;
}) {
  const { t } = useTranslation();
  const [note, setNote] = useState("");
  const [needReason, setNeedReason] = useState(false);
  const closed = c.status === "closed";
  const trimmed = note.trim() || undefined;

  const owner =
    c.assigned_to === null ? t("case.verdict.unassigned") : c.assigned_to === me ? t("alerts.you") : t("alerts.someoneElse");

  return (
    <Section
      title={t("case.verdict.title")}
      action={<span className="text-xs text-muted-foreground">{t("case.verdict.owner", { owner })}</span>}
    >
      {closed ? (
        <div className="rounded-lg border bg-muted/50 p-3.5 text-sm" role="status">
          <p className="flex items-center gap-2 font-semibold">
            {c.label === "fraud" ? (
              <ShieldAlert className="size-4 text-risk-high" aria-hidden="true" />
            ) : (
              <CircleCheck className="size-4 text-risk-low" aria-hidden="true" />
            )}
            {t(c.label === "fraud" ? "case.verdict.closedFraud" : "case.verdict.closedLegit")}
          </p>
          {c.resolved_at && (
            <p className="mt-1 text-muted-foreground">
              {t("case.verdict.resolvedAt", {
                time: formatDateTime(c.resolved_at),
                who: c.resolved_by === me ? t("alerts.you") : t("alerts.someoneElse"),
              })}
            </p>
          )}
          {c.notes && <p className="mt-2 whitespace-pre-wrap border-t pt-2">{c.notes}</p>}
          <Link to="/dashboard/alerts" className={cn(buttonVariants({ variant: "outline", size: "sm" }), "mt-3")}>
            <ArrowLeft aria-hidden="true" />
            {t("case.backToQueue")}
          </Link>
        </div>
      ) : (
        <div className="space-y-3">
          {c.notes && (
            <p className="whitespace-pre-wrap rounded-lg bg-muted/60 px-3 py-2 text-sm">
              <span className="block text-xs text-muted-foreground">{t("case.verdict.savedNote")}</span>
              {c.notes}
            </p>
          )}
          <div>
            <label htmlFor="case-note" className="text-xs font-medium text-muted-foreground">
              {t("case.verdict.note")}
            </label>
            <textarea
              id="case-note"
              value={note}
              onChange={(e) => {
                setNote(e.target.value);
                if (e.target.value.trim()) setNeedReason(false);
              }}
              maxLength={4000}
              rows={3}
              placeholder={t("case.verdict.notePlaceholder")}
              aria-invalid={needReason || undefined}
              aria-describedby={needReason ? "case-note-error" : undefined}
              className={cn(
                "mt-1 w-full resize-y rounded-lg border bg-card px-3 py-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/20",
                needReason && "border-destructive",
              )}
            />
            {needReason && (
              <p id="case-note-error" className="mt-1 text-xs text-destructive">
                {t("case.verdict.reasonNeeded")}
              </p>
            )}
          </div>
          <div className="grid gap-2">
            <Button variant="destructive" disabled={pending !== null} onClick={() => onVerdict("fraud", trimmed)}>
              <ShieldAlert aria-hidden="true" />
              {pending === "fraud" ? t("case.verdict.saving") : t("case.verdict.fraud")}
            </Button>
            <Button variant="outline" disabled={pending !== null} onClick={() => onVerdict("legit", trimmed)}>
              <CircleCheck aria-hidden="true" />
              {pending === "legit" ? t("case.verdict.saving") : t("case.verdict.legit")}
            </Button>
            <Button
              variant="ghost"
              disabled={pending !== null}
              onClick={() => (trimmed ? onVerdict("escalate", trimmed) : setNeedReason(true))}
            >
              <Megaphone aria-hidden="true" />
              {pending === "escalate" ? t("case.verdict.saving") : t("case.verdict.escalate")}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">{t("case.verdict.escalateHint")}</p>
        </div>
      )}
    </Section>
  );
}

/**
 * Spec 06 Case Detail: everything an analyst needs to decide one held payment (payment, the
 * explanation that the worker's LLM sentence replaces live, SHAP waterfall, device and context,
 * the customer's recent payments) and the decision itself, applied optimistically.
 */
export function CaseDetailPage() {
  const { t, i18n } = useTranslation();
  const { caseId = "" } = useParams();
  const { state } = useAuth();
  const me = state.status === "authenticated" ? state.user.id : "";
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const lang = (i18n.resolvedLanguage ?? "en") as Language;
  const [liveExplanation, setLiveExplanation] = useState<{ txnId: string; text: string } | null>(null);

  const key = caseKey(caseId);
  const caseQuery = useQuery({ queryKey: key, queryFn: () => getCase(caseId) });
  const txnId = caseQuery.data?.transaction.txn_id;

  const explain = useQuery({
    queryKey: ["explain", txnId, lang],
    queryFn: () => explainTransaction(txnId ?? "", lang),
    enabled: txnId !== undefined,
  });
  const detail = useQuery({
    queryKey: ["transaction", txnId],
    queryFn: () => getTransaction(txnId ?? ""),
    enabled: txnId !== undefined,
  });

  const onExplanation = useCallback(
    (ready: { txn_id: string; explanation: string }) => {
      if (ready.txn_id === txnId) setLiveExplanation({ txnId: ready.txn_id, text: ready.explanation });
    },
    [txnId],
  );
  useAlertStream(undefined, onExplanation);

  const verdict = useMutation({
    mutationFn: ({ verdict: v, note }: { verdict: Verdict; note: string | undefined }) => updateCase(caseId, bodyFor(v, note)),
    onMutate: async ({ verdict: v, note }) => {
      await queryClient.cancelQueries({ queryKey: key });
      await queryClient.cancelQueries({ queryKey: QUEUE_KEY });
      const before = queryClient.getQueryData<CaseDetail>(key);
      const queue = queryClient.getQueryData<Page<AlertItem>>(QUEUE_KEY);
      const body = bodyFor(v, note);
      queryClient.setQueryData<CaseDetail>(key, (c) =>
        c && {
          ...c,
          status: body.status ?? c.status,
          label: body.label ?? c.label,
          severity: body.severity ?? c.severity,
          notes: body.notes ?? c.notes,
          assigned_to: "assigned_to" in body ? (body.assigned_to ?? null) : c.assigned_to,
          resolved_by: body.label ? me : c.resolved_by,
          resolved_at: body.label ? new Date().toISOString() : c.resolved_at,
        },
      );
      queryClient.setQueryData<Page<AlertItem>>(QUEUE_KEY, (page) => {
        if (!page || !before) return page;
        if (body.status === "closed") {
          // a closed alert leaves the queue at once (spec 06 §4)
          const items = page.items.filter((a) => a.alert_id !== before.alert_id);
          return { ...page, items, total: page.total - (page.items.length - items.length) };
        }
        return {
          ...page,
          items: page.items.map((a) =>
            a.alert_id === before.alert_id ? { ...a, severity: "high" as const, status: "open" as const, assigned_to: null } : a,
          ),
        };
      });
      return { before, queue };
    },
    onError: (error, _vars, context) => {
      if (context?.before) queryClient.setQueryData(key, context.before);
      if (context?.queue) queryClient.setQueryData(QUEUE_KEY, context.queue);
      toast({ tone: "error", title: t("case.toast.failed"), body: toApiError(error).message });
    },
    onSuccess: (saved, { verdict: v }) => {
      queryClient.setQueryData(key, saved);
      toast({
        tone: "success",
        title: t(`case.toast.${v}`),
        body: t("case.toast.body", { txn: saved.transaction.txn_id }),
      });
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: QUEUE_KEY }),
  });

  const back = (
    <Link
      to="/dashboard/alerts"
      className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="size-4" aria-hidden="true" />
      {t("case.backToQueue")}
    </Link>
  );

  if (caseQuery.isError) {
    const notFound = caseQuery.error instanceof ApiError && caseQuery.error.status === 404;
    return (
      <div className="space-y-4">
        {back}
        {notFound ? (
          <EmptyState icon={FileQuestion} title={t("case.notFound")} body={t("case.notFoundBody")} className="min-h-72" />
        ) : (
          <ErrorState
            title={t("case.error")}
            message={toApiError(caseQuery.error).message}
            onRetry={() => void caseQuery.refetch()}
            className="min-h-[50vh]"
          />
        )}
      </div>
    );
  }

  const c = caseQuery.data;
  if (!c) {
    return (
      <div className="space-y-4" aria-busy="true">
        {back}
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="space-y-4 lg:col-span-2">
            <div className="h-56 animate-pulse rounded-xl bg-muted/70" />
            <div className="h-40 animate-pulse rounded-xl bg-muted/70" />
          </div>
          <div className="h-72 animate-pulse rounded-xl bg-muted/70" />
        </div>
      </div>
    );
  }

  const explanation =
    liveExplanation?.txnId === c.transaction.txn_id ? liveExplanation.text : (explain.data?.explanation || c.explanation);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {back}
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <SeverityBadge severity={c.severity} />
          <Badge variant={c.status === "closed" ? "muted" : "outline"}>{t(`case.status.${c.status}`)}</Badge>
          <span className="text-muted-foreground">{t("case.raised", { time: formatDateTime(c.created_at) })}</span>
          <Link
            to="/dashboard/live"
            className="hidden items-center gap-1 text-[13px] font-medium text-primary hover:underline sm:inline-flex"
          >
            {t("nav.live")}
            <ArrowUpRight className="size-3.5" aria-hidden="true" />
          </Link>
        </div>
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-3">
        <div className="min-w-0 space-y-4 lg:col-span-2">
          <PaymentCard c={c} />
          <ExplanationCard
            text={explanation}
            live={liveExplanation?.txnId === c.transaction.txn_id}
            reasons={c.top_reasons}
          />
          {explain.isError ? (
            <ErrorState
              title={t("case.shap.error")}
              message={toApiError(explain.error).message}
              onRetry={() => void explain.refetch()}
            />
          ) : explain.data ? (
            <ShapWaterfall shap={explain.data.shap} />
          ) : (
            <div className="h-80 animate-pulse rounded-xl bg-muted/70" aria-busy="true" />
          )}
        </div>
        <div className="min-w-0 space-y-4">
          <VerdictCard
            c={c}
            me={me}
            pending={verdict.isPending ? verdict.variables.verdict : null}
            onVerdict={(v, note) => verdict.mutate({ verdict: v, note })}
          />
          <DeviceCard txn={detail.data} loading={detail.isPending} />
          <HistoryCard customerId={c.transaction.customer.id} currentTxn={c.transaction.txn_id} />
        </div>
      </div>
    </div>
  );
}
