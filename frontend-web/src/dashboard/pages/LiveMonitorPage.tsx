import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Inbox, Pause, Play, SearchX } from "lucide-react";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { ConnectionStatus } from "@/dashboard/components/ConnectionStatus";
import { Segmented } from "@/dashboard/components/Segmented";
import { useAlertStream } from "@/dashboard/hooks/useAlertStream";
import { EMPTY_FEED, feedReducer, LIVE_LIMIT } from "@/dashboard/lib/liveFeed";
import { toApiError } from "@/shared/api/client";
import { getTransaction, listTransactions } from "@/shared/api/endpoints/transactions";
import { DecisionBadge } from "@/shared/components/DecisionBadge";
import { EmptyState } from "@/shared/components/EmptyState";
import { ErrorState } from "@/shared/components/ErrorState";
import { RiskBadge } from "@/shared/components/RiskBadge";
import { Button } from "@/shared/components/ui/button";
import { Card } from "@/shared/components/ui/card";
import { cn } from "@/shared/lib/cn";
import { formatInr, formatTime, maskVpa } from "@/shared/lib/format";
import type { AlertCreatedMessage, Channel, Decision, TxnItem } from "@/shared/types/api";

/** Payments other than HOLDs have no push message (spec 04 §3), so they are polled. */
export const POLL_MS = 3_000;
const POLL_SIZE = 100;
/** Re-ask a little before the newest payment seen, in case two arrived in the same instant. */
const OVERLAP_MS = 10_000;

type DecisionFilter = "all" | Decision;
type ChannelFilter = "all" | Channel;

function SkeletonRows() {
  return (
    <div className="space-y-2 p-4" aria-busy="true">
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="h-9 animate-pulse rounded-md bg-muted/70" />
      ))}
    </div>
  );
}

function FeedTable({ rows, caseOf }: { rows: TxnItem[]; caseOf: ReadonlyMap<string, string> }) {
  const { t } = useTranslation();
  const head = "sticky top-0 z-10 bg-card px-3 py-2.5 text-left text-xs font-medium text-muted-foreground";
  return (
    <table className="w-full min-w-[960px] text-sm">
      <thead>
        <tr className="shadow-[inset_0_-1px_0_var(--border)]">
          <th scope="col" className={head}>{t("live.col.time")}</th>
          <th scope="col" className={head}>{t("live.col.txn")}</th>
          <th scope="col" className={head}>{t("live.col.customer")}</th>
          <th scope="col" className={head}>{t("live.col.payee")}</th>
          <th scope="col" className={cn(head, "text-right")}>{t("live.col.amount")}</th>
          <th scope="col" className={head}>{t("live.col.channel")}</th>
          <th scope="col" className={head}>{t("live.col.risk")}</th>
          <th scope="col" className={head}>{t("live.col.decision")}</th>
          <th scope="col" className={cn(head, "text-right")}>{t("live.col.latency")}</th>
          <th scope="col" className={head}><span className="sr-only">{t("live.openCase")}</span></th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const caseId = caseOf.get(r.txn_id);
          return (
            <tr
              key={r.id}
              className={cn(
                "border-t",
                r.decision === "HOLD" && "bg-risk-high/[0.04]",
                "animate-row-in", // plays when a row first mounts, i.e. when it arrives
              )}
            >
              <td className="whitespace-nowrap px-3 py-2 tabular-nums text-muted-foreground">{formatTime(r.ts)}</td>
              <td className="whitespace-nowrap px-3 py-2 font-mono text-[12.5px]">{r.txn_id}</td>
              <td className="whitespace-nowrap px-3 py-2">{r.customer_external_id ?? "—"}</td>
              <td className="whitespace-nowrap px-3 py-2">{maskVpa(r.payee_vpa)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right font-medium tabular-nums">{formatInr(r.amount)}</td>
              <td className="px-3 py-2">{t(`channels.${r.channel}`)}</td>
              <td className="px-3 py-2">{r.risk_score != null ? <RiskBadge score={r.risk_score} /> : "—"}</td>
              <td className="whitespace-nowrap px-3 py-2">{r.decision ? <DecisionBadge decision={r.decision} /> : "—"}</td>
              <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                {r.latency_ms != null ? `${Math.round(r.latency_ms)} ms` : "—"}
              </td>
              <td className="px-3 py-2 text-right">
                {caseId && (
                  <Link
                    to={`/dashboard/cases/${caseId}`}
                    className="inline-flex items-center gap-1 whitespace-nowrap text-[13px] font-medium text-primary hover:underline"
                  >
                    {t("live.openCase")}
                    <ArrowUpRight className="size-3.5" aria-hidden="true" />
                  </Link>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/**
 * Spec 06 §3 Live Monitor: the newest scored payments as they happen. HOLDs arrive over
 * `/ws/alerts` (< 1 s); the rest come from polling `/transactions` every 3 s. Pause freezes the
 * list and counts what came in meanwhile.
 */
export function LiveMonitorPage() {
  const { t } = useTranslation();
  const [feed, dispatch] = useReducer(feedReducer, EMPTY_FEED);
  const [decision, setDecision] = useState<DecisionFilter>("all");
  const [channel, setChannel] = useState<ChannelFilter>("all");
  const [caseOf, setCaseOf] = useState<ReadonlyMap<string, string>>(new Map());
  const newest = useRef<number | null>(null);

  const add = useCallback((rows: TxnItem[]) => {
    for (const r of rows) newest.current = Math.max(newest.current ?? 0, Date.parse(r.ts));
    dispatch({ type: "add", rows });
  }, []);

  const poll = useQuery({
    queryKey: ["live-transactions"],
    queryFn: () =>
      listTransactions({
        page_size: POLL_SIZE,
        ...(newest.current != null && { from: new Date(newest.current - OVERLAP_MS).toISOString() }),
      }),
    refetchInterval: POLL_MS,
    gcTime: 0, // a revisit starts a fresh feed
  });
  useEffect(() => {
    if (poll.data) add(poll.data.items);
  }, [poll.data, add]);

  const onAlert = useCallback(
    (alert: AlertCreatedMessage["data"]) => {
      setCaseOf((m) => new Map(m).set(alert.txn_id, alert.case_id));
      getTransaction(alert.txn_id).then(
        (txn) => add([txn]),
        () => undefined, // the next poll brings it
      );
    },
    [add],
  );
  const status = useAlertStream(onAlert);

  const loaded = poll.isFetched;
  const visible = feed.rows.filter(
    (r) => (decision === "all" || r.decision === decision) && (channel === "all" || r.channel === channel),
  );
  const filtered = decision !== "all" || channel !== "all";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented<DecisionFilter>
          label={t("live.filters.decision")}
          value={decision}
          onChange={setDecision}
          options={[
            { value: "all", label: t("live.filters.all") },
            { value: "HOLD", label: t("decisions.HOLD") },
            { value: "STEP_UP", label: t("decisions.STEP_UP") },
            { value: "APPROVE", label: t("decisions.APPROVE") },
          ]}
        />
        <Segmented<ChannelFilter>
          label={t("live.filters.channel")}
          value={channel}
          onChange={setChannel}
          options={[
            { value: "all", label: t("live.filters.all") },
            { value: "UPI", label: t("channels.UPI") },
            { value: "CARD", label: t("channels.CARD") },
            { value: "WALLET", label: t("channels.WALLET") },
          ]}
        />
        <div className="ml-auto flex items-center gap-3">
          <ConnectionStatus status={status} hint={t("connection.hint", { seconds: POLL_MS / 1000 })} />
          {feed.paused ? (
            <Button size="sm" onClick={() => dispatch({ type: "resume" })}>
              <Play aria-hidden="true" />
              {t("live.resume")}
              {feed.pending.length > 0 && (
                <span className="rounded-full bg-primary-foreground/20 px-1.5 text-xs tabular-nums">
                  {feed.pending.length}
                </span>
              )}
            </Button>
          ) : (
            <Button size="sm" variant="outline" onClick={() => dispatch({ type: "pause" })}>
              <Pause aria-hidden="true" />
              {t("live.pause")}
            </Button>
          )}
        </div>
      </div>

      {feed.paused && (
        <p role="status" className="rounded-lg border border-primary/25 bg-secondary px-3.5 py-2.5 text-sm">
          {t("live.paused")}
          {feed.pending.length > 0 && (
            <strong className="ml-2 font-semibold">{t("live.newWhilePaused", { count: feed.pending.length })}</strong>
          )}
        </p>
      )}
      {poll.isError && feed.rows.length > 0 && (
        <p role="status" className="rounded-lg border border-risk-medium/40 bg-risk-medium/10 px-3.5 py-2.5 text-sm">
          {t("live.staleError")}
        </p>
      )}

      {poll.isError && feed.rows.length === 0 ? (
        <ErrorState
          title={t("live.error")}
          message={toApiError(poll.error).message}
          onRetry={() => void poll.refetch()}
          className="min-h-[50vh]"
        />
      ) : !loaded && feed.rows.length === 0 ? (
        <Card><SkeletonRows /></Card>
      ) : feed.rows.length === 0 ? (
        <EmptyState icon={Inbox} title={t("live.empty")} body={t("live.emptyBody")} className="min-h-72" />
      ) : visible.length === 0 && filtered ? (
        <EmptyState
          icon={SearchX}
          title={t("live.noMatch")}
          className="min-h-72"
          action={
            <Button variant="outline" onClick={() => { setDecision("all"); setChannel("all"); }}>
              {t("live.clearFilters")}
            </Button>
          }
        />
      ) : (
        <Card className="overflow-hidden">
          <div className="max-h-[calc(100dvh-15rem)] overflow-auto">
            <FeedTable rows={visible} caseOf={caseOf} />
          </div>
          <p className="border-t px-4 py-2.5 text-xs text-muted-foreground">
            {t("live.showing", { shown: visible.length, limit: LIVE_LIMIT })}
          </p>
        </Card>
      )}
    </div>
  );
}
