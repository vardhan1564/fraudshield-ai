/**
 * Graph Explorer helpers (T5.3): merging neighbourhoods as nodes are expanded, ring membership,
 * which node ids may go in the page URL, and where newly loaded nodes are placed.
 */
import type { GraphEdge, GraphNode, GraphNodeType, Neighbourhood, RingRef } from "@/shared/types/api";

export const EMPTY_GRAPH: Neighbourhood = { nodes: [], edges: [], rings: [], truncated: false };

/** Stop expanding past this many nodes: the canvas stays smooth and the picture readable. */
export const MAX_VIEW_NODES = 600;

/** The detector flags the graph refresh writes (spec 01 F3), in display order. */
export const FLAGS = ["mule_suspect", "gnn", "fan_in", "pass_through", "shared_device", "new_account_burst"] as const;
export type Flag = (typeof FLAGS)[number];

export function isKnownFlag(flag: string): flag is Flag {
  return (FLAGS as readonly string[]).includes(flag);
}

const TYPES: Record<string, GraphNodeType> = { A: "Account", P: "Payee", D: "Device", I: "IP" };

/** `A:U1029` → Account; undefined for anything that is not a graph id. */
export function nodeType(id: string): GraphNodeType | undefined {
  return id[1] === ":" && id.length > 2 ? TYPES[id[0] ?? ""] : undefined;
}

/**
 * What the analyst typed → a graph id. A bare id is an account (`U1029` → `A:U1029`); a full
 * graph id (`P:refund.help@okbank`) is kept as typed.
 */
export function toNodeId(input: string): string | null {
  const value = input.trim();
  if (!value) return null;
  if (nodeType(value)) return value;
  return /^[\w-]{1,64}$/.test(value) ? `A:${value}` : null;
}

/**
 * Only account ids (an external id or UUID, not personal data) may go in the page URL.
 * Payee VPAs, device fingerprints and IPs stay in page state (CLAUDE.md rule 8).
 */
export function urlSafe(id: string): boolean {
  return nodeType(id) === "Account";
}

export function edgeKey(e: GraphEdge): string {
  return `${e.source}|${e.target}|${e.type}|${e.ts}`;
}

/** Union of two neighbourhoods; the newer copy of a node or ring wins (fresher risk/flags). */
export function mergeGraphs(a: Neighbourhood, b: Neighbourhood): Neighbourhood {
  const nodes = new Map(a.nodes.map((n) => [n.id, n]));
  for (const n of b.nodes) nodes.set(n.id, n);
  const edges = new Map(a.edges.map((e) => [edgeKey(e), e]));
  for (const e of b.edges) edges.set(edgeKey(e), e);
  const rings = new Map(a.rings.map((r) => [r.id, r]));
  for (const r of b.rings) rings.set(r.id, r);
  return {
    nodes: [...nodes.values()],
    edges: [...edges.values()],
    rings: [...rings.values()].sort((x, y) => y.score - x.score),
    truncated: a.truncated || b.truncated,
  };
}

/** node id → the rings it belongs to (highest score first), for rings in view. */
export function ringsByNode(rings: RingRef[]): Map<string, RingRef[]> {
  const out = new Map<string, RingRef[]>();
  for (const ring of [...rings].sort((x, y) => y.score - x.score)) {
    for (const member of ring.members) out.set(member, [...(out.get(member) ?? []), ring]);
  }
  return out;
}

/** Edges whose two ends are in the same ring: drawn as part of the ring. */
export function ringEdgeKeys(graph: Neighbourhood): Set<string> {
  const keys = new Set<string>();
  const members = graph.rings.map((r) => new Set(r.members));
  for (const e of graph.edges) {
    if (members.some((m) => m.has(e.source) && m.has(e.target))) keys.add(edgeKey(e));
  }
  return keys;
}

export interface NodeSummary {
  sentCount: number;
  sentAmount: number;
  receivedCount: number;
  receivedAmount: number;
  linked: number; // devices / IPs / accounts linked by a non-payment edge
}

/** Payments in and out of a node among the edges in view. */
export function summarise(id: string, edges: GraphEdge[]): NodeSummary {
  const s: NodeSummary = { sentCount: 0, sentAmount: 0, receivedCount: 0, receivedAmount: 0, linked: 0 };
  for (const e of edges) {
    if (e.type !== "SENT") {
      if (e.source === id || e.target === id) s.linked += 1;
    } else if (e.source === id) {
      s.sentCount += 1;
      s.sentAmount += e.amount ?? 0;
    } else if (e.target === id) {
      s.receivedCount += 1;
      s.receivedAmount += e.amount ?? 0;
    }
  }
  return s;
}

/** Highest risk first; unscored nodes (devices, IPs) last. */
export function byRisk(a: GraphNode, b: GraphNode): number {
  return (b.risk ?? -1) - (a.risk ?? -1) || a.label.localeCompare(b.label);
}

/**
 * Positions for nodes loaded by an expansion: on circles around the node that was clicked,
 * so the existing picture does not move under the analyst's cursor.
 */
export function placeAround(
  center: { x: number; y: number },
  count: number,
  spacing = 34,
): { x: number; y: number }[] {
  const out: { x: number; y: number }[] = [];
  let ring = 1;
  while (out.length < count) {
    const radius = ring * spacing * 2.2;
    const slots = Math.max(6, Math.floor((2 * Math.PI * radius) / spacing));
    const take = Math.min(slots, count - out.length);
    const offset = ring * 0.5; // stagger rings so spokes do not line up
    for (let i = 0; i < take; i += 1) {
      const angle = offset + (2 * Math.PI * i) / take;
      out.push({ x: center.x + radius * Math.cos(angle), y: center.y + radius * Math.sin(angle) });
    }
    ring += 1;
  }
  return out;
}
