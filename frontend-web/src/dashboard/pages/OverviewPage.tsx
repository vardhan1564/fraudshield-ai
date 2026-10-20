import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Activity, Gauge, Inbox, Percent, ShieldAlert, Wallet } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ChartCard, DataTable } from "@/dashboard/components/ChartCard";
import { CityTable } from "@/dashboard/components/CityTable";
import { FraudByTypeChart, type TypeRow } from "@/dashboard/components/FraudByTypeChart";
import { HourlyCharts } from "@/dashboard/components/HourlyCharts";
import { KpiCard } from "@/dashboard/components/KpiCard";
import { RANGE_MS, type RangeKey } from "@/dashboard/components/range";
import { RangePicker } from "@/dashboard/components/RangePicker";
import { toApiError } from "@/shared/api/client";
import { metricsOverview } from "@/shared/api/endpoints/metrics";
import { EmptyState } from "@/shared/components/EmptyState";
import { ErrorState } from "@/shared/components/ErrorState";
import { formatInr, formatInrCompact, formatNumber, formatPercent } from "@/shared/lib/format";
import type { MetricsOverview } from "@/shared/types/api";

const REFRESH_MS = 30_000;
const LATENCY_BUDGET_MS = 100;

function Kpis({ m }: { m: MetricsOverview | undefined }) {
  const { t } = useTranslation();
  const loading = m === undefined;
  const p95 = m?.p95_latency_ms ?? null;
  return (
    <section className="grid gap-4 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-5" aria-label="KPIs">
      <KpiCard
        loading={loading}
        icon={Activity}
        label={t("overview.kpi.payments")}
        value={m ? formatNumber(m.txn_count) : null}
        hint={t("overview.kpi.paymentsHint")}
      />
      <KpiCard
        loading={loading}
        icon={ShieldAlert}
        label={t("overview.kpi.caught")}
        value={m ? formatNumber(m.fraud_caught) : null}
        hint={t("overview.kpi.caughtHint", {
          rate: m && m.txn_count ? formatPercent(m.fraud_caught / m.txn_count, 2) : "0%",
        })}
      />
      <KpiCard
        loading={loading}
        icon={Wallet}
        label={t("overview.kpi.saved")}
        value={m ? formatInrCompact(m.amount_saved_inr) : null}
        fullValue={m ? formatInr(m.amount_saved_inr) : undefined}
        hint={t("overview.kpi.savedHint")}
      />
      <KpiCard
        loading={loading}
        icon={Percent}
        label={t("overview.kpi.fpr")}
        value={m?.false_positive_rate != null ? formatPercent(m.false_positive_rate, 2) : null}
        hint={m?.false_positive_rate != null ? t("overview.kpi.fprHint") : t("overview.kpi.fprNone")}
      />
      <KpiCard
        loading={loading}
        icon={Gauge}
        label={t("overview.kpi.p95")}
        value={p95 != null ? `${Math.round(p95)} ms` : null}
        hint={t("overview.kpi.noData")}
        status={
          p95 == null
            ? undefined
            : p95 <= LATENCY_BUDGET_MS
              ? { tone: "good", label: t("overview.kpi.withinBudget") }
              : { tone: "critical", label: t("overview.kpi.overBudget") }
        }
      />
    </section>
  );
}

function ChartSkeleton({ height }: { height: number }) {
  return (
    <div className="rounded-xl border bg-card p-5" aria-busy="true">
      <div className="h-4 w-40 animate-pulse rounded bg-muted" />
      <div className="mt-2 h-3 w-56 animate-pulse rounded bg-muted" />
      <div className="mt-5 animate-pulse rounded-lg bg-muted/70" style={{ height }} />
    </div>
  );
}

/** Spec 06 §3 Overview: 5 KPIs, activity by hour, fraud by type, top cities (GET /metrics/overview). */
export function OverviewPage() {
  const { t } = useTranslation();
  const [range, setRange] = useState<RangeKey>("24h");
  const query = useQuery({
    queryKey: ["metrics-overview", range],
    queryFn: () => metricsOverview({ from: new Date(Date.now() - RANGE_MS[range]).toISOString() }),
    refetchInterval: REFRESH_MS,
    placeholderData: keepPreviousData, // a new range keeps the old frame, faded
  });
  const m = query.data;
  const dimmed = query.isFetching && query.isPlaceholderData;

  const types: TypeRow[] = (m?.by_type ?? [])
    .map((r) => ({
      key: r.scenario,
      name: t(`scenarios.${r.scenario}`, { defaultValue: r.scenario.replaceAll("_", " ") }),
      count: r.count,
    }))
    .sort((a, b) => b.count - a.count);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <RangePicker value={range} onChange={setRange} />
        {m && (
          <p className="text-[13px] text-muted-foreground" aria-live="polite">
            {query.isFetching
              ? t("overview.refreshing")
              : t("overview.updated", {
                  time: new Date(query.dataUpdatedAt).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }),
                })}
          </p>
        )}
      </div>

      {query.isError && !m ? (
        <ErrorState
          title={t("overview.error")}
          message={toApiError(query.error).message}
          onRetry={() => void query.refetch()}
          className="min-h-[50vh]"
        />
      ) : (
        <>
          {query.isError && m && (
            <p role="status" className="rounded-lg border border-risk-medium/40 bg-risk-medium/10 px-3.5 py-2.5 text-sm">
              {t("overview.staleError")}
            </p>
          )}
          <Kpis m={m} />

          {!m ? (
            <div className="grid gap-4 lg:grid-cols-5">
              <div className="lg:col-span-3"><ChartSkeleton height={300} /></div>
              <div className="lg:col-span-2"><ChartSkeleton height={300} /></div>
            </div>
          ) : m.txn_count === 0 ? (
            <EmptyState icon={Inbox} title={t("overview.empty")} body={t("overview.emptyBody")} className="min-h-72" />
          ) : (
            <>
              <div className="grid gap-4 lg:grid-cols-5">
                <ChartCard
                  className="lg:col-span-3"
                  dimmed={dimmed}
                  title={t("overview.hourly.title")}
                  subtitle={t("overview.hourly.subtitle")}
                  chart={() => <HourlyCharts rows={m.by_hour} />}
                  table={() => (
                    <DataTable
                      head={[t("overview.hourly.hour"), t("overview.hourly.volume"), t("overview.hourly.flagged")]}
                      rows={m.by_hour.map((r) => [`${String(r.hour).padStart(2, "0")}:00`, formatNumber(r.total), formatNumber(r.flagged)])}
                    />
                  )}
                />
                <ChartCard
                  className="lg:col-span-2"
                  dimmed={dimmed}
                  title={t("overview.types.title")}
                  subtitle={t("overview.types.subtitle")}
                  chart={types.length ? () => <FraudByTypeChart rows={types} /> : undefined}
                  table={() => (
                    <DataTable
                      head={[t("overview.types.type"), t("overview.types.count")]}
                      rows={types.map((r) => [r.name, formatNumber(r.count)])}
                    />
                  )}
                />
              </div>
              <ChartCard
                dimmed={dimmed}
                title={t("overview.cities.title")}
                subtitle={t("overview.cities.subtitle")}
                table={() => <CityTable rows={m.by_city} />}
              />
            </>
          )}
        </>
      )}
    </div>
  );
}
