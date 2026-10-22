import { useQuery } from "@tanstack/react-query";
import { Network, Search, SearchX, Store } from "lucide-react";
import { type FormEvent, type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import { ChartCard, DataTable } from "@/dashboard/components/ChartCard";
import { FraudByTypeChart, type TypeRow } from "@/dashboard/components/FraudByTypeChart";
import { toApiError } from "@/shared/api/client";
import { payeeReputation } from "@/shared/api/endpoints/payees";
import { EmptyState } from "@/shared/components/EmptyState";
import { ErrorState } from "@/shared/components/ErrorState";
import { RiskBadge } from "@/shared/components/RiskBadge";
import { Button } from "@/shared/components/ui/button";
import { Card } from "@/shared/components/ui/card";
import { formatDateTime, formatNumber } from "@/shared/lib/format";
import type { PayeeReputation, ScamType } from "@/shared/types/api";

const VPA = /^[a-z0-9][a-z0-9._-]{1,63}@[a-z][a-z0-9.-]{1,63}$/;
const SCAM_TYPES: readonly ScamType[] = [
  "digital_arrest", "fake_support_refund", "investment_task", "fake_kyc", "lottery_prize", "job_task", "phishing", "other",
];

function useScamName() {
  const { t } = useTranslation();
  return (type: string) =>
    (SCAM_TYPES as readonly string[]).includes(type) ? t(`customer.report.types.${type as ScamType}`) : type;
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-lg font-semibold tabular-nums">{children}</dd>
    </div>
  );
}

function Result({ rep }: { rep: PayeeReputation }) {
  const { t } = useTranslation();
  const scamName = useScamName();
  const rows: TypeRow[] = Object.entries(rep.reports_by_type)
    .map(([key, count]) => ({ key, name: scamName(key), count }))
    .sort((a, b) => b.count - a.count);
  const recent = rep.recent_reports ?? [];

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-mono text-base font-semibold">{rep.vpa}</h2>
          <RiskBadge score={rep.reputation_score} />
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-4 border-t pt-4 sm:grid-cols-4">
          <Fact label={t("payees.reports")}>{formatNumber(rep.report_count)}</Fact>
          <Fact label={t("payees.confirmed")}>{formatNumber(rep.confirmed_fraud_count)}</Fact>
          <Fact label={t("payees.age")}>
            {rep.account_age_days === null ? "—" : t("payees.days", { count: rep.account_age_days })}
          </Fact>
          <Fact label={t("payees.graph")}>
            <span className="inline-flex items-center gap-1.5 text-base">
              <Network className="size-4 text-muted-foreground" aria-hidden="true" />
              {rep.graph_flag ? t("payees.graphFlagged") : t("payees.graphClear")}
            </span>
          </Fact>
        </dl>
        <p className="mt-4 text-[13px] text-muted-foreground">{t("payees.scoreRule")}</p>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard
          title={t("payees.byType")}
          chart={rows.length ? () => <FraudByTypeChart rows={rows} label={t("payees.byType")} totalLabel={t("payees.reports")} /> : undefined}
          table={() =>
            rows.length ? (
              <DataTable head={[t("payees.type"), t("payees.reports")]} rows={rows.map((r) => [r.name, r.count])} />
            ) : (
              <p className="py-8 text-center text-sm text-muted-foreground">{t("payees.noReports")}</p>
            )
          }
        />
        <Card className="p-5">
          <h2 className="text-[15px] font-semibold">{t("payees.recent")}</h2>
          {recent.length === 0 ? (
            <p className="mt-6 text-center text-sm text-muted-foreground">{t("payees.noReports")}</p>
          ) : (
            <ul className="mt-3 divide-y">
              {recent.map((r) => (
                <li key={r.report_id} className="py-2.5 text-sm">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="font-medium">{scamName(r.scam_type)}</span>
                    <span className="text-xs text-muted-foreground">{formatDateTime(r.created_at)}</span>
                  </div>
                  {r.phone && <p className="mt-0.5 font-mono text-[12.5px] text-muted-foreground">{t("payees.caller", { phone: r.phone })}</p>}
                  {r.description && <p className="mt-1 text-muted-foreground">{r.description}</p>}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

/**
 * Spec 06 §3 Payee Reputation (analyst): look up a UPI ID and see what customers reported
 * (spec 01 F7). The VPA stays in page state, never in the page URL (CLAUDE.md rule 8).
 */
export function PayeesPage() {
  const { t } = useTranslation();
  const [input, setInput] = useState("");
  const [vpa, setVpa] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(false);
  const reputation = useQuery({
    queryKey: ["reputation", vpa],
    queryFn: () => payeeReputation(vpa ?? ""),
    enabled: vpa !== null,
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const value = input.trim().toLowerCase();
    const ok = VPA.test(value);
    setInvalid(!ok);
    if (ok) setVpa(value);
  };

  return (
    <div className="space-y-4">
      <form onSubmit={onSubmit} noValidate className="flex flex-wrap items-start gap-2" role="search">
        <div className="min-w-64 flex-1 sm:max-w-md">
          <label htmlFor="payee-search" className="sr-only">{t("payees.search")}</label>
          <input
            id="payee-search"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={t("payees.placeholder")}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={invalid || undefined}
            aria-describedby={invalid ? "payee-search-error" : undefined}
            className="h-10 w-full rounded-lg border bg-card px-3 font-mono text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/20 aria-[invalid=true]:border-destructive"
          />
          {invalid && <p id="payee-search-error" className="mt-1 text-[13px] text-destructive">{t("payees.invalid")}</p>}
        </div>
        <Button type="submit">
          <Search aria-hidden="true" />
          {t("payees.search")}
        </Button>
      </form>

      {vpa === null ? (
        <EmptyState icon={Store} title={t("payees.start")} body={t("payees.startBody")} className="min-h-72" />
      ) : reputation.isError ? (
        <ErrorState title={t("payees.error")} message={toApiError(reputation.error).message} onRetry={() => void reputation.refetch()} />
      ) : !reputation.data ? (
        <div className="h-64 animate-pulse rounded-xl bg-muted/70" aria-busy="true" />
      ) : !reputation.data.known ? (
        <EmptyState icon={SearchX} title={t("payees.unknown", { vpa })} body={t("payees.unknownBody")} className="min-h-60" />
      ) : (
        <Result rep={reputation.data} />
      )}
    </div>
  );
}
