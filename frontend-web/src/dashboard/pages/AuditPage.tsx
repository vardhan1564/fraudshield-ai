import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronLeft, ChevronRight, Download, Loader2, ScrollText } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { toApiError } from "@/shared/api/client";
import { AUDIT_ACTIONS, AUDIT_ENTITIES, type AuditFilters, downloadAuditCsv, listAudit } from "@/shared/api/endpoints/audit";
import { EmptyState } from "@/shared/components/EmptyState";
import { ErrorState } from "@/shared/components/ErrorState";
import { Button } from "@/shared/components/ui/button";
import { Card } from "@/shared/components/ui/card";
import { useAuth } from "@/shared/hooks/useAuth";
import { useToast } from "@/shared/hooks/useToast";
import { cn } from "@/shared/lib/cn";
import { formatDateTime, formatNumber } from "@/shared/lib/format";
import type { AuditEntry } from "@/shared/types/api";

const PAGE_SIZE = 50;
const control = "h-9 rounded-lg border bg-card px-2.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/20";

/** A `<input type="date">` day as the start of that day in the admin's own time zone. */
function dayStart(day: string): string | undefined {
  return day ? new Date(`${day}T00:00:00`).toISOString() : undefined;
}

/** `to` is exclusive in the API, so "until 21 Oct" means before 22 Oct 00:00. */
function dayEnd(day: string): string | undefined {
  if (!day) return undefined;
  const next = new Date(`${day}T00:00:00`);
  next.setDate(next.getDate() + 1);
  return next.toISOString();
}

function Json({ label, value }: { label: string; value: Record<string, unknown> | null }) {
  return (
    <div className="min-w-0">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <pre className="mt-1 max-h-60 overflow-auto rounded-lg border bg-card p-2.5 font-mono text-[12px]">
        {value ? JSON.stringify(value, null, 2) : "—"}
      </pre>
    </div>
  );
}

function Row({ entry, me, open, onToggle }: { entry: AuditEntry; me: string; open: boolean; onToggle: () => void }) {
  const { t } = useTranslation();
  const hasDetail = entry.before_json !== null || entry.after_json !== null;
  const cell = "px-3 py-2.5";
  return (
    <>
      <tr className="border-t">
        <td className={cn(cell, "whitespace-nowrap tabular-nums text-muted-foreground")}>{formatDateTime(entry.ts)}</td>
        <td className={cn(cell, "whitespace-nowrap font-mono text-[12.5px]")}>{entry.action}</td>
        <td className={cell}>{t(`audit.entities.${entry.entity as (typeof AUDIT_ENTITIES)[number]}`, { defaultValue: entry.entity })}</td>
        <td className={cn(cell, "max-w-48 truncate font-mono text-[12px]")} title={entry.entity_id ?? undefined}>{entry.entity_id ?? "—"}</td>
        <td className={cn(cell, "whitespace-nowrap")}>
          {entry.actor_id === null ? "—" : entry.actor_id === me ? t("alerts.you") : <span className="font-mono text-[12px]" title={entry.actor_id}>{entry.actor_id.slice(0, 8)}…</span>}
        </td>
        <td className={cn(cell, "font-mono text-[12px] text-muted-foreground")}>{entry.ip ?? "—"}</td>
        <td className={cn(cell, "text-right")}>
          {hasDetail && (
            <Button size="sm" variant="ghost" onClick={onToggle} aria-expanded={open} aria-label={t("audit.details", { id: entry.id })}>
              <ChevronDown className={cn("transition-transform", open && "rotate-180")} aria-hidden="true" />
            </Button>
          )}
        </td>
      </tr>
      {open && (
        <tr className="bg-muted/30">
          <td colSpan={7} className="px-3 pb-3 pt-1">
            <div className="grid gap-3 md:grid-cols-2">
              <Json label={t("audit.before")} value={entry.before_json} />
              <Json label={t("audit.after")} value={entry.after_json} />
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

/**
 * Spec 06 §3 Audit Log (admin): every recorded change and sign-in, newest first, filtered by entity,
 * action, "only mine" and date range; each row opens to its before/after; CSV export of all matches.
 */
export function AuditPage() {
  const { t } = useTranslation();
  const { state } = useAuth();
  const { toast } = useToast();
  const me = state.status === "authenticated" ? state.user.id : "";
  const [entity, setEntity] = useState("");
  const [action, setAction] = useState("");
  const [mine, setMine] = useState(false);
  const [fromDay, setFromDay] = useState("");
  const [toDay, setToDay] = useState("");
  const [page, setPage] = useState(1);
  const [openRow, setOpenRow] = useState<number | null>(null);

  const filters: AuditFilters = {
    entity: entity || undefined,
    action: action || undefined,
    actor: mine ? me : undefined,
    from: dayStart(fromDay),
    to: dayEnd(toDay),
  };
  const log = useQuery({
    queryKey: ["audit", filters, page],
    queryFn: () => listAudit({ ...filters, page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });
  const csv = useMutation({
    mutationFn: () => downloadAuditCsv(filters),
    onError: (error) => toast({ tone: "error", title: t("audit.csvFailed"), body: toApiError(error).message }),
  });

  const changed = <T,>(set: (v: T) => void) => (v: T) => { set(v); setPage(1); setOpenRow(null); };
  const total = log.data?.total ?? 0;
  const first = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const last = Math.min(page * PAGE_SIZE, total);
  const filtered = Boolean(entity || action || mine || fromDay || toDay);
  const head = "px-3 py-2.5 text-left text-xs font-medium text-muted-foreground";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="audit-entity" className="block text-xs font-medium text-muted-foreground">{t("audit.filters.entity")}</label>
          <select id="audit-entity" value={entity} onChange={(e) => changed(setEntity)(e.target.value)} className={cn(control, "mt-1 block")}>
            <option value="">{t("audit.filters.any")}</option>
            {AUDIT_ENTITIES.map((v) => <option key={v} value={v}>{t(`audit.entities.${v}`)}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="audit-action" className="block text-xs font-medium text-muted-foreground">{t("audit.filters.action")}</label>
          <select id="audit-action" value={action} onChange={(e) => changed(setAction)(e.target.value)} className={cn(control, "mt-1 block font-mono")}>
            <option value="">{t("audit.filters.any")}</option>
            {AUDIT_ACTIONS.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="audit-from" className="block text-xs font-medium text-muted-foreground">{t("audit.filters.from")}</label>
          <input id="audit-from" type="date" value={fromDay} max={toDay || undefined} onChange={(e) => changed(setFromDay)(e.target.value)} className={cn(control, "mt-1 block")} />
        </div>
        <div>
          <label htmlFor="audit-to" className="block text-xs font-medium text-muted-foreground">{t("audit.filters.to")}</label>
          <input id="audit-to" type="date" value={toDay} min={fromDay || undefined} onChange={(e) => changed(setToDay)(e.target.value)} className={cn(control, "mt-1 block")} />
        </div>
        <label className="flex h-9 items-center gap-2 text-sm">
          <input type="checkbox" checked={mine} onChange={(e) => changed(setMine)(e.target.checked)} className="size-4 accent-[var(--primary)]" />
          {t("audit.filters.mine")}
        </label>
        {filtered && (
          <Button variant="ghost" size="sm" onClick={() => { setEntity(""); setAction(""); setMine(false); setFromDay(""); setToDay(""); setPage(1); }}>
            {t("live.clearFilters")}
          </Button>
        )}
        <Button variant="outline" className="ml-auto" disabled={csv.isPending || total === 0} onClick={() => csv.mutate()}>
          {csv.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Download aria-hidden="true" />}
          {t("audit.csv")}
        </Button>
      </div>

      {log.isError && !log.data ? (
        <ErrorState title={t("audit.error")} message={toApiError(log.error).message} onRetry={() => void log.refetch()} className="min-h-72" />
      ) : !log.data ? (
        <Card><div className="space-y-2 p-4" aria-busy="true">{Array.from({ length: 8 }, (_, i) => <div key={i} className="h-9 animate-pulse rounded-md bg-muted/70" />)}</div></Card>
      ) : log.data.items.length === 0 ? (
        <EmptyState icon={ScrollText} title={filtered ? t("audit.noMatch") : t("audit.empty")} className="min-h-60" />
      ) : (
        <Card className={cn("overflow-hidden transition-opacity", log.isFetching && "opacity-70")}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[920px] text-sm">
              <thead>
                <tr className="border-b">
                  <th scope="col" className={head}>{t("audit.col.time")}</th>
                  <th scope="col" className={head}>{t("audit.col.action")}</th>
                  <th scope="col" className={head}>{t("audit.col.entity")}</th>
                  <th scope="col" className={head}>{t("audit.col.entityId")}</th>
                  <th scope="col" className={head}>{t("audit.col.actor")}</th>
                  <th scope="col" className={head}>{t("audit.col.ip")}</th>
                  <th scope="col" className={head}><span className="sr-only">{t("audit.col.details")}</span></th>
                </tr>
              </thead>
              <tbody>
                {log.data.items.map((entry) => (
                  <Row key={entry.id} entry={entry} me={me} open={openRow === entry.id} onToggle={() => setOpenRow((o) => (o === entry.id ? null : entry.id))} />
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between border-t px-4 py-2.5 text-xs text-muted-foreground">
            <span aria-live="polite">{t("audit.showing", { first: formatNumber(first), last: formatNumber(last), total: formatNumber(total) })}</span>
            <span className="inline-flex gap-1">
              <Button size="sm" variant="ghost" disabled={page === 1} onClick={() => setPage((p) => p - 1)} aria-label={t("audit.prev")}>
                <ChevronLeft aria-hidden="true" />
              </Button>
              <Button size="sm" variant="ghost" disabled={last >= total} onClick={() => setPage((p) => p + 1)} aria-label={t("audit.next")}>
                <ChevronRight aria-hidden="true" />
              </Button>
            </span>
          </div>
        </Card>
      )}
    </div>
  );
}
