import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlarmClock,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ArrowUpRight,
  BellOff,
  Clock,
  TriangleAlert,
  UserPlus,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { ConnectionStatus } from "@/dashboard/components/ConnectionStatus";
import { Segmented } from "@/dashboard/components/Segmented";
import { ALERTS_KEY, useAlertStream } from "@/dashboard/hooks/useAlertStream";
import { slaState, type SlaTone } from "@/dashboard/lib/sla";
import { DEFAULT_SORT, nextSort, type Sort, sortAlerts, type SortKey } from "@/dashboard/lib/sortAlerts";
import { toApiError } from "@/shared/api/client";
import { listAlerts, updateCase } from "@/shared/api/endpoints/cases";
import { EmptyState } from "@/shared/components/EmptyState";
import { ErrorState } from "@/shared/components/ErrorState";
import { RiskBadge } from "@/shared/components/RiskBadge";
import { Badge } from "@/shared/components/ui/badge";
import { Button } from "@/shared/components/ui/button";
import { Card } from "@/shared/components/ui/card";
import { useAuth } from "@/shared/hooks/useAuth";
import { useNow } from "@/shared/hooks/useNow";
import { cn } from "@/shared/lib/cn";
import { formatDuration, formatInr } from "@/shared/lib/format";
import { RISK_CLASSES } from "@/shared/lib/riskColor";
import type { AlertItem, Page, Severity } from "@/shared/types/api";

const QUEUE_KEY = [...ALERTS_KEY, "queue"] as const;
const QUEUE_SIZE = 200; // the API's page limit
const REFRESH_MS = 30_000; // backup for a dropped socket; the socket triggers refetches itself

type View = "all" | "unassigned" | "mine";

const SLA_LOOK: Record<SlaTone, { icon: typeof Clock; className: string }> = {
  ok: { icon: Clock, className: "text-muted-foreground" },
  soon: { icon: AlarmClock, className: "font-semibold" },
  overdue: { icon: TriangleAlert, className: "font-semibold text-risk-high" },
};

function SeverityBadge({ severity }: { severity: Severity }) {
  const { t } = useTranslation();
  return (
    <span className="inline-flex items-center gap-2 text-[13px] font-medium">
      <span className={cn("size-2 rounded-full", RISK_CLASSES[severity].dot)} aria-hidden="true" />
      {t(`severity.${severity}`)}
    </span>
  );
}

function SlaCell({ alert, now }: { alert: AlertItem; now: number }) {
  const { t } = useTranslation();
  const { remainingS, tone } = slaState(alert.created_at, alert.severity, now);
  const { icon: Icon, className } = SLA_LOOK[tone];
  const time = formatDuration(Math.abs(remainingS));
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap tabular-nums", className)}>
      <Icon className={cn("size-3.5 shrink-0", tone === "soon" && "text-risk-medium")} aria-hidden="true" />
      {t(`alerts.sla.${tone}`, { time })}
    </span>
  );
}

function SortHeader({ column, label, sort, onSort, align = "left" }: {
  column: SortKey;
  label: string;
  sort: Sort;
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
}) {
  const { t } = useTranslation();
  const active = sort.key === column;
  const Icon = !active ? ArrowUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <th
      scope="col"
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
      className={cn("sticky top-0 z-10 bg-card px-3 py-1.5", align === "right" && "text-right")}
    >
      <button
        type="button"
        onClick={() => onSort(column)}
        title={t("alerts.sortBy", { column: label })}
        className={cn(
          "inline-flex h-8 cursor-pointer items-center gap-1 rounded-md px-1.5 -mx-1.5 text-xs font-medium hover:bg-muted",
          active ? "text-foreground" : "text-muted-foreground",
        )}
      >
        {label}
        <Icon className={cn("size-3.5", !active && "opacity-50")} aria-hidden="true" />
      </button>
    </th>
  );
}

function OwnerCell({ alert, me, pending, onAssign }: {
  alert: AlertItem;
  me: string;
  pending: boolean;
  onAssign: () => void;
}) {
  const { t } = useTranslation();
  if (alert.assigned_to === me) return <Badge>{t("alerts.you")}</Badge>;
  if (alert.assigned_to) return <span className="text-[13px] text-muted-foreground">{t("alerts.someoneElse")}</span>;
  return (
    <Button size="sm" variant="outline" disabled={pending || !alert.case_id} onClick={onAssign}>
      <UserPlus aria-hidden="true" />
      {pending ? t("alerts.assigning") : t("alerts.assignToMe")}
    </Button>
  );
}

/**
 * Spec 06 §3 Alert Queue: open and assigned alerts from `/alerts`, refreshed when `/ws/alerts`
 * says something changed. Sortable, assign-to-me (optimistic), and an SLA countdown per alert.
 */
export function AlertQueuePage() {
  const { t } = useTranslation();
  const { state } = useAuth();
  const me = state.status === "authenticated" ? state.user.id : "";
  const queryClient = useQueryClient();
  const now = useNow();
  const [view, setView] = useState<View>("all");
  const [sort, setSort] = useState<Sort>(DEFAULT_SORT);
  const [assignError, setAssignError] = useState<string | null>(null);

  const status = useAlertStream();
  const query = useQuery({
    queryKey: QUEUE_KEY,
    queryFn: () => listAlerts({ page_size: QUEUE_SIZE }),
    refetchInterval: REFRESH_MS,
  });

  const assign = useMutation({
    mutationFn: (alert: AlertItem) => updateCase(alert.case_id ?? "", { assigned_to: me }),
    onMutate: async (alert) => {
      setAssignError(null);
      await queryClient.cancelQueries({ queryKey: QUEUE_KEY });
      const before = queryClient.getQueryData<Page<AlertItem>>(QUEUE_KEY);
      queryClient.setQueryData<Page<AlertItem>>(QUEUE_KEY, (page) =>
        page && {
          ...page,
          items: page.items.map((a) =>
            a.alert_id === alert.alert_id ? { ...a, assigned_to: me, status: "assigned" as const } : a,
          ),
        },
      );
      return { before };
    },
    onError: (error, _alert, context) => {
      if (context?.before) queryClient.setQueryData(QUEUE_KEY, context.before);
      setAssignError(t("alerts.assignFailed", { message: toApiError(error).message }));
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ALERTS_KEY }),
  });

  const all = query.data?.items ?? [];
  const shown = sortAlerts(
    all.filter((a) => view === "all" || (view === "mine" ? a.assigned_to === me : a.assigned_to === null)),
    sort,
  );
  const onSort = (key: SortKey) => setSort((s) => nextSort(s, key));
  const head = "sticky top-0 z-10 bg-card px-3 py-2.5 text-left text-xs font-medium text-muted-foreground";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented<View>
          label={t("alerts.view.label")}
          value={view}
          onChange={setView}
          options={[
            { value: "all", label: t("alerts.view.all") },
            { value: "unassigned", label: t("alerts.view.unassigned") },
            { value: "mine", label: t("alerts.view.mine") },
          ]}
        />
        {query.data && (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {t("alerts.count", { count: query.data.total })}
          </p>
        )}
        <div className="ml-auto flex items-center gap-3">
          <p className="hidden text-xs text-muted-foreground xl:block">{t("alerts.sla.rule")}</p>
          <ConnectionStatus status={status} />
        </div>
      </div>

      {assignError && (
        <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 px-3.5 py-2.5 text-sm">
          {assignError}
        </p>
      )}

      {query.isError && !query.data ? (
        <ErrorState
          title={t("alerts.error")}
          message={toApiError(query.error).message}
          onRetry={() => void query.refetch()}
          className="min-h-[50vh]"
        />
      ) : !query.data ? (
        <Card>
          <div className="space-y-2 p-4" aria-busy="true">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="h-11 animate-pulse rounded-md bg-muted/70" />
            ))}
          </div>
        </Card>
      ) : shown.length === 0 ? (
        <EmptyState
          icon={BellOff}
          title={
            view === "mine" ? t("alerts.emptyMine") : view === "unassigned" ? t("alerts.emptyUnassigned") : t("alerts.empty")
          }
          body={view === "all" ? t("alerts.emptyBody") : undefined}
          className="min-h-72"
        />
      ) : (
        <Card className="overflow-hidden">
          <div className="max-h-[calc(100dvh-13rem)] overflow-auto">
            <table className="w-full min-w-[1040px] text-sm">
              <thead>
                <tr className="shadow-[inset_0_-1px_0_var(--border)]">
                  <SortHeader column="severity" label={t("alerts.col.severity")} sort={sort} onSort={onSort} />
                  <th scope="col" className={head}>{t("alerts.col.txn")}</th>
                  <SortHeader column="amount" label={t("alerts.col.amount")} sort={sort} onSort={onSort} align="right" />
                  <SortHeader column="risk" label={t("alerts.col.risk")} sort={sort} onSort={onSort} />
                  <th scope="col" className={head}>{t("alerts.col.reason")}</th>
                  <SortHeader column="age" label={t("alerts.col.age")} sort={sort} onSort={onSort} />
                  <SortHeader column="sla" label={t("alerts.col.sla")} sort={sort} onSort={onSort} />
                  <th scope="col" className={head}>{t("alerts.col.owner")}</th>
                  <th scope="col" className={head}><span className="sr-only">{t("alerts.open")}</span></th>
                </tr>
              </thead>
              <tbody>
                {shown.map((a) => {
                  const overdue = slaState(a.created_at, a.severity, now).tone === "overdue";
                  return (
                    <tr key={a.alert_id} className={cn("border-t", overdue && "bg-risk-high/[0.05]")}>
                      <td className="px-3 py-2.5"><SeverityBadge severity={a.severity} /></td>
                      <td className="whitespace-nowrap px-3 py-2.5 font-mono text-[12.5px]">{a.txn_id}</td>
                      <td className="whitespace-nowrap px-3 py-2.5 text-right font-medium tabular-nums">{formatInr(a.amount)}</td>
                      <td className="px-3 py-2.5">{a.risk_score != null ? <RiskBadge score={a.risk_score} /> : "—"}</td>
                      <td className="max-w-72 px-3 py-2.5">
                        <span className="line-clamp-1" title={a.top_reasons.map((r) => r.text).join(" · ")}>
                          {a.top_reasons[0]?.text ?? "—"}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2.5 tabular-nums text-muted-foreground">
                        {formatDuration((now - Date.parse(a.created_at)) / 1000)}
                      </td>
                      <td className="px-3 py-2.5"><SlaCell alert={a} now={now} /></td>
                      <td className="px-3 py-2.5">
                        <OwnerCell
                          alert={a}
                          me={me}
                          pending={assign.isPending && assign.variables.alert_id === a.alert_id}
                          onAssign={() => assign.mutate(a)}
                        />
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {a.case_id && (
                          <Link
                            to={`/dashboard/cases/${a.case_id}`}
                            aria-label={`${t("alerts.open")} ${a.txn_id}`}
                            className="inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline"
                          >
                            {t("alerts.open")}
                            <ArrowUpRight className="size-3.5" aria-hidden="true" />
                          </Link>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
