import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Crosshair, Loader2, Network, Search, SearchX, X } from "lucide-react";
import { type FormEvent, type ReactNode, useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";

import { GraphView } from "@/dashboard/components/GraphView";
import { Segmented } from "@/dashboard/components/Segmented";
import {
  byRisk,
  EMPTY_GRAPH,
  isKnownFlag,
  MAX_VIEW_NODES,
  mergeGraphs,
  ringsByNode,
  summarise,
  toNodeId,
  urlSafe,
} from "@/dashboard/lib/graph";
import { toApiError } from "@/shared/api/client";
import { graphEntity, listRings } from "@/shared/api/endpoints/graph";
import { listTransactions } from "@/shared/api/endpoints/transactions";
import { EmptyState } from "@/shared/components/EmptyState";
import { ErrorState } from "@/shared/components/ErrorState";
import { RiskBadge } from "@/shared/components/RiskBadge";
import { Button } from "@/shared/components/ui/button";
import { Card } from "@/shared/components/ui/card";
import { useToast } from "@/shared/hooks/useToast";
import { cn } from "@/shared/lib/cn";
import { formatDateTime, formatInr, formatNumber } from "@/shared/lib/format";
import type { GraphNode, Neighbourhood, RingItem } from "@/shared/types/api";

type Depth = "1" | "2" | "3";
type View = "graph" | "table";

function useFlagName() {
  const { t } = useTranslation();
  return (flag: string) => (isKnownFlag(flag) ? t(`graph.flags.${flag}`) : flag === "community" ? t("graph.community") : flag);
}

function Flags({ flags }: { flags: string[] }) {
  const flagName = useFlagName();
  return (
    <span className="flex flex-wrap gap-1">
      {flags.map((f) => (
        <span key={f} className="rounded-md bg-muted px-1.5 py-0.5 text-[11.5px] font-medium text-muted-foreground">
          {flagName(f)}
        </span>
      ))}
    </span>
  );
}

function Risk({ risk }: { risk: number | null }) {
  const { t } = useTranslation();
  return risk === null ? <span className="text-xs text-muted-foreground">{t("graph.noScore")}</span> : <RiskBadge score={risk} />;
}

function PanelSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t pt-3">
      <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{title}</h3>
      <div className="mt-2">{children}</div>
    </section>
  );
}

/** Held payments of an account or payee, each linking to its case (spec 06 §3 side panel). */
function RelatedCases({ node }: { node: GraphNode }) {
  const { t } = useTranslation();
  const ref = node.id.slice(2);
  const cases = useQuery({
    queryKey: ["graph-cases", node.id],
    queryFn: () =>
      listTransactions({
        ...(node.type === "Account" ? { user_id: ref } : { payee_vpa: ref }),
        decision: "HOLD",
        page_size: 5,
      }),
    enabled: node.type === "Account" || node.type === "Payee",
  });
  if (node.type !== "Account" && node.type !== "Payee") {
    return <p className="text-sm text-muted-foreground">{t("graph.casesNa")}</p>;
  }
  if (cases.isError) {
    return (
      <p className="text-sm text-destructive">
        {t("graph.casesError")}{" "}
        <button type="button" className="cursor-pointer underline" onClick={() => void cases.refetch()}>
          {t("common.retry")}
        </button>
      </p>
    );
  }
  if (!cases.data) return <div className="h-16 animate-pulse rounded-lg bg-muted/70" aria-busy="true" />;
  if (cases.data.items.length === 0) return <p className="text-sm text-muted-foreground">{t("graph.noCases")}</p>;
  return (
    <ul className="divide-y text-sm">
      {cases.data.items.map((txn) => (
        <li key={txn.id} className="flex items-center justify-between gap-2 py-2">
          <span>
            <span className="font-medium tabular-nums">{formatInr(txn.amount)}</span>
            <span className="block text-xs text-muted-foreground">{formatDateTime(txn.ts)}</span>
          </span>
          {txn.case_id ? (
            <Link
              to={`/dashboard/cases/${txn.case_id}`}
              className="inline-flex items-center gap-1 text-[13px] font-medium text-primary hover:underline"
              aria-label={t("graph.openCaseFor", { txn: txn.txn_id })}
            >
              {t("graph.openCase")}
              <ArrowRight className="size-3.5" aria-hidden="true" />
            </Link>
          ) : (
            <span className="text-xs text-muted-foreground">{t("graph.noCase")}</span>
          )}
        </li>
      ))}
      {cases.data.total > cases.data.items.length && (
        <li className="pt-2 text-xs text-muted-foreground">{t("graph.moreCases", { count: cases.data.total - cases.data.items.length })}</li>
      )}
    </ul>
  );
}

function NodePanel({
  node,
  graph,
  isFocus,
  onFocus,
  onClose,
}: {
  node: GraphNode;
  graph: Neighbourhood;
  isFocus: boolean;
  onFocus: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const rings = ringsByNode(graph.rings).get(node.id) ?? [];
  const s = summarise(node.id, graph.edges);
  return (
    <Card className="space-y-3 p-4" aria-labelledby="graph-node-title">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs text-muted-foreground">{t(`graph.types.${node.type}`)}</p>
          <h2 id="graph-node-title" className="truncate font-mono text-[15px] font-semibold">{node.label}</h2>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label={t("graph.close")}>
          <X aria-hidden="true" />
        </Button>
      </div>
      <Risk risk={node.risk} />

      <PanelSection title={t("graph.signals")}>
        {node.flags.length ? <Flags flags={node.flags} /> : <p className="text-sm text-muted-foreground">{t("graph.noFlags")}</p>}
        {rings.map((r) => (
          <p key={r.id} className="mt-2 flex items-center gap-1.5 text-sm">
            <span className="size-2.5 rounded-full bg-graph-ring" aria-hidden="true" />
            {t("graph.memberOf", { count: r.members.length, score: r.score.toFixed(2) })}
          </p>
        ))}
      </PanelSection>

      <PanelSection title={t("graph.inView")}>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
          <div>
            <dt className="text-xs text-muted-foreground">{t("graph.paidOut")}</dt>
            <dd className="tabular-nums">{t("graph.payments", { count: s.sentCount })} · {formatInr(s.sentAmount)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">{t("graph.received")}</dt>
            <dd className="tabular-nums">{t("graph.payments", { count: s.receivedCount })} · {formatInr(s.receivedAmount)}</dd>
          </div>
          <div className="col-span-2">
            <dt className="text-xs text-muted-foreground">{t("graph.linked")}</dt>
            <dd className="tabular-nums">{formatNumber(s.linked)}</dd>
          </div>
        </dl>
      </PanelSection>

      <PanelSection title={t("graph.cases")}>
        <RelatedCases node={node} />
      </PanelSection>

      {!isFocus && (
        <Button variant="outline" className="w-full" onClick={onFocus}>
          <Crosshair aria-hidden="true" />
          {t("graph.focusHere")}
        </Button>
      )}
    </Card>
  );
}

function RingList({ rings, onOpen, compact = false }: { rings: RingItem[]; onOpen: (ring: RingItem) => void; compact?: boolean }) {
  const { t } = useTranslation();
  const flagName = useFlagName();
  return (
    <ul className="divide-y">
      {rings.map((ring) => (
        <li key={ring.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
          <div className="min-w-0 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <RiskBadge score={ring.score} />
              <span className="font-medium">{t("graph.ringSize", { count: ring.size })}</span>
              <span className="text-muted-foreground">· {flagName(ring.detector)}</span>
            </div>
            {!compact && (
              <p className="mt-0.5 text-xs text-muted-foreground">
                {t(`graph.statuses.${ring.status}`)} · {t("graph.lastSeen", { when: formatDateTime(ring.last_seen) })}
              </p>
            )}
          </div>
          <Button size="sm" variant="outline" onClick={() => onOpen(ring)} aria-label={t("graph.openRingAria", { count: ring.size, score: ring.score.toFixed(2) })}>
            {t("graph.openRing")}
          </Button>
        </li>
      ))}
    </ul>
  );
}

function NodeTable({ graph, selectedId, onSelect }: { graph: Neighbourhood; selectedId: string | null; onSelect: (id: string) => void }) {
  const { t } = useTranslation();
  const ringNodes = ringsByNode(graph.rings);
  const nodes = [...graph.nodes].sort(byRisk);
  return (
    <div className="size-full overflow-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-muted/90 backdrop-blur">
          <tr className="text-left text-muted-foreground">
            <th scope="col" className="px-3 py-2 font-medium">{t("graph.table.node")}</th>
            <th scope="col" className="px-3 py-2 font-medium">{t("graph.table.type")}</th>
            <th scope="col" className="px-3 py-2 font-medium">{t("graph.table.risk")}</th>
            <th scope="col" className="px-3 py-2 font-medium">{t("graph.signals")}</th>
          </tr>
        </thead>
        <tbody>
          {nodes.map((n) => (
            <tr key={n.id} className={cn("border-t", n.id === selectedId && "bg-primary/8")}>
              <td className="px-3 py-1.5">
                <button
                  type="button"
                  className="inline-flex cursor-pointer items-center gap-1.5 font-mono text-[13px] hover:underline"
                  onClick={() => onSelect(n.id)}
                  aria-label={t("graph.selectNode", { label: n.label })}
                >
                  {ringNodes.has(n.id) && <span className="size-2 rounded-full bg-graph-ring" aria-hidden="true" />}
                  {n.label}
                </button>
              </td>
              <td className="px-3 py-1.5">{t(`graph.types.${n.type}`)}</td>
              <td className="px-3 py-1.5"><Risk risk={n.risk} /></td>
              <td className="px-3 py-1.5"><Flags flags={n.flags} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Legend() {
  const { t } = useTranslation();
  const dot = "inline-block size-2.5 rounded-full";
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t px-3 py-2 text-xs text-muted-foreground">
      <span className="inline-flex items-center gap-1.5"><span className={cn(dot, "bg-risk-low")} />{t("severity.low")}</span>
      <span className="inline-flex items-center gap-1.5"><span className={cn(dot, "bg-risk-medium")} />{t("severity.medium")}</span>
      <span className="inline-flex items-center gap-1.5"><span className={cn(dot, "bg-risk-high")} />{t("severity.high")}</span>
      <span className="inline-flex items-center gap-1.5"><span className={cn(dot, "bg-muted-foreground")} />{t("graph.noScore")}</span>
      <span className="inline-flex items-center gap-1.5"><span className={cn(dot, "ring-4 ring-graph-ring/45 bg-risk-high")} />{t("graph.legendRing")}</span>
      <span>{t("graph.legendShapes")}</span>
      <span>{t("graph.legendLines")}</span>
    </div>
  );
}

interface Expansion {
  key: string;
  parts: Neighbourhood[];
  expanded: string[];
}

/**
 * Spec 06 §3 Graph Explorer (analyst, T5.3): a node's neighbourhood from `/graph/entity/{id}`
 * drawn with Cytoscape, suspected rings highlighted, click a node to load its neighbours and see
 * its details and related cases. Only account ids go in the URL; payee, phone and IP ids stay in
 * page state (CLAUDE.md rule 8).
 */
export function GraphExplorerPage() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { entityId } = useParams();
  const [hiddenFocus, setHiddenFocus] = useState<string | null>(null);
  const focus = entityId ?? hiddenFocus;
  const [depth, setDepth] = useState<Depth>("2");
  const [view, setView] = useState<View>("graph");
  const [input, setInput] = useState("");
  const [invalid, setInvalid] = useState(false);
  const key = `${focus ?? ""}|${depth}`;
  const [expansion, setExpansion] = useState<Expansion>({ key, parts: [], expanded: [] });
  const [selection, setSelection] = useState<{ key: string; id: string | null; origin: string | null }>({ key, id: null, origin: null });
  const [expanding, setExpanding] = useState<string | null>(null);

  // The last picture stays on screen while another depth or node loads (no blank canvas, no
  // rebuild); `shownKey` says which focus/depth the data on screen belongs to.
  const base = useQuery({
    queryKey: ["graph", focus, Number(depth)],
    queryFn: async () => ({ key, graph: await graphEntity(focus ?? "", Number(depth)) }),
    enabled: focus !== null,
    placeholderData: keepPreviousData,
  });
  const shownKey = base.data?.key ?? key;
  const rings = useQuery({ queryKey: ["rings"], queryFn: () => listRings({ status: "suspected", page_size: 50 }) });

  const expanded = useMemo(() => new Set(expansion.key === shownKey ? expansion.expanded : []), [expansion, shownKey]);
  const graph = useMemo(
    () => (expansion.key === shownKey ? expansion.parts : []).reduce(mergeGraphs, base.data?.graph ?? EMPTY_GRAPH),
    [expansion, shownKey, base.data],
  );
  const selectedId = selection.key === shownKey ? (selection.id ?? focus) : focus;
  const origin = selection.key === shownKey ? selection.origin : null;
  const selected = graph.nodes.find((n) => n.id === selectedId) ?? null;

  const open = useCallback(
    (id: string) => {
      if (urlSafe(id)) {
        setHiddenFocus(null);
        void navigate(`/dashboard/graph/${encodeURIComponent(id)}`);
      } else {
        setHiddenFocus(id);
        if (entityId) void navigate("/dashboard/graph");
      }
    },
    [entityId, navigate],
  );

  const onNodeClick = useCallback(
    (id: string) => {
      setSelection({ key: shownKey, id, origin: id });
      if (id === focus || expanded.has(id) || expanding || base.isPlaceholderData) return;
      if (graph.nodes.length >= MAX_VIEW_NODES) {
        toast({ tone: "info", title: t("graph.full", { count: graph.nodes.length }) });
        return;
      }
      const label = graph.nodes.find((n) => n.id === id)?.label ?? id;
      setExpanding(id);
      queryClient
        .fetchQuery({ queryKey: ["graph", id, 1], queryFn: () => graphEntity(id, 1) })
        .then((part) =>
          setExpansion((prev) => {
            const same = prev.key === shownKey ? prev : { key: shownKey, parts: [], expanded: [] };
            return { key: shownKey, parts: [...same.parts, part], expanded: [...same.expanded, id] };
          }),
        )
        .catch((error: unknown) => toast({ tone: "error", title: t("graph.expandError", { label }), body: toApiError(error).message }))
        .finally(() => setExpanding(null));
    },
    [shownKey, focus, expanded, expanding, base.isPlaceholderData, graph.nodes, queryClient, toast, t],
  );

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const id = toNodeId(input);
    setInvalid(id === null);
    if (id) open(id);
  };

  const openRing = (ring: RingItem) => {
    const first = ring.members[0];
    if (first) open(first);
  };

  const baseError = base.isError ? toApiError(base.error) : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start gap-3">
        <form onSubmit={onSubmit} noValidate className="flex min-w-72 flex-1 items-start gap-2 sm:max-w-lg" role="search">
          <div className="flex-1">
            <label htmlFor="graph-search" className="sr-only">{t("graph.searchLabel")}</label>
            <input
              id="graph-search"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={t("graph.placeholder")}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={invalid || undefined}
              aria-describedby={invalid ? "graph-search-error" : undefined}
              className="h-10 w-full rounded-lg border bg-card px-3 font-mono text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/20 aria-[invalid=true]:border-destructive"
            />
            {invalid && <p id="graph-search-error" className="mt-1 text-[13px] text-destructive">{t("graph.invalid")}</p>}
          </div>
          <Button type="submit">
            <Search aria-hidden="true" />
            {t("graph.open")}
          </Button>
        </form>
        {focus !== null && (
          <div className="flex flex-wrap gap-2">
            <Segmented
              label={t("graph.depth")}
              value={depth}
              onChange={setDepth}
              options={(["1", "2", "3"] as const).map((d) => ({ value: d, label: t("graph.hops", { count: Number(d) }) }))}
            />
            <Segmented
              label={t("graph.view")}
              value={view}
              onChange={setView}
              options={[
                { value: "graph", label: t("graph.viewGraph") },
                { value: "table", label: t("graph.viewTable") },
              ]}
            />
          </div>
        )}
      </div>

      {focus === null ? (
        <Card className="p-5">
          <h2 className="text-[15px] font-semibold">{t("graph.rings")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("graph.startBody")}</p>
          <div className="mt-3">
            {rings.isError ? (
              <ErrorState title={t("graph.ringsError")} message={toApiError(rings.error).message} onRetry={() => void rings.refetch()} />
            ) : !rings.data ? (
              <div className="h-40 animate-pulse rounded-lg bg-muted/70" aria-busy="true" />
            ) : rings.data.items.length === 0 ? (
              <EmptyState icon={Network} title={t("graph.noRings")} body={t("graph.noRingsBody")} className="min-h-48" />
            ) : (
              <RingList rings={rings.data.items} onOpen={openRing} />
            )}
          </div>
        </Card>
      ) : baseError?.status === 404 ? (
        <EmptyState icon={SearchX} title={t("graph.notFound")} body={t("graph.notFoundBody")} className="min-h-72" />
      ) : baseError ? (
        <ErrorState title={t("graph.error")} message={baseError.message} onRetry={() => void base.refetch()} />
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_21rem]">
          <Card className="flex h-[clamp(440px,72vh,780px)] flex-col overflow-hidden p-0">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2 text-xs text-muted-foreground">
              <span aria-live="polite">
                {base.data && !base.isPlaceholderData
                  ? t("graph.summary", { nodes: graph.nodes.length, edges: graph.edges.length, rings: t("graph.ringCount", { count: graph.rings.length }) })
                  : t("graph.loading")}
              </span>
              <span className="inline-flex items-center gap-1.5">
                {expanding || base.isFetching ? (
                  <>
                    <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                    {t("graph.expanding")}
                  </>
                ) : (
                  t("graph.hint")
                )}
              </span>
            </div>
            {graph.truncated && (
              <p className="border-b bg-risk-medium/10 px-3 py-1.5 text-xs text-foreground" role="note">
                {t("graph.truncated")}
              </p>
            )}
            <div className="relative min-h-0 flex-1">
              {!base.data ? (
                <div className="absolute inset-3 animate-pulse rounded-lg bg-muted/70" aria-busy="true" />
              ) : view === "graph" ? (
                <GraphView
                  graph={graph}
                  graphKey={shownKey}
                  focusId={focus}
                  selectedId={selectedId}
                  origin={origin}
                  onNodeClick={onNodeClick}
                  label={t("graph.canvas", { nodes: graph.nodes.length, rings: graph.rings.length })}
                />
              ) : (
                <NodeTable graph={graph} selectedId={selectedId} onSelect={onNodeClick} />
              )}
            </div>
            <Legend />
          </Card>

          <aside className="space-y-4">
            {selected ? (
              <NodePanel
                node={selected}
                graph={graph}
                isFocus={selected.id === focus}
                onFocus={() => open(selected.id)}
                onClose={() => setSelection({ key: shownKey, id: null, origin: null })}
              />
            ) : null}
            <Card className="p-4">
              <h2 className="text-[15px] font-semibold">{t("graph.ringsInView")}</h2>
              {graph.rings.length === 0 ? (
                <p className="mt-2 text-sm text-muted-foreground">{t("graph.noRingsInView")}</p>
              ) : (
                <ul className="mt-2 space-y-1.5 text-sm">
                  {graph.rings.map((r) => (
                    <li key={r.id} className="flex items-center justify-between gap-2">
                      <span className="inline-flex items-center gap-1.5">
                        <span className="size-2.5 rounded-full bg-graph-ring" aria-hidden="true" />
                        {t("graph.ringSize", { count: r.members.length })}
                      </span>
                      <RiskBadge score={r.score} />
                    </li>
                  ))}
                </ul>
              )}
              <Button variant="link" className="mt-2 h-auto px-0" onClick={() => { setHiddenFocus(null); void navigate("/dashboard/graph"); }}>
                {t("graph.allRings")}
              </Button>
            </Card>
          </aside>
        </div>
      )}
    </div>
  );
}
