import { describe, expect, it } from "vitest";

import {
  byRisk,
  EMPTY_GRAPH,
  mergeGraphs,
  nodeType,
  placeAround,
  ringEdgeKeys,
  ringsByNode,
  summarise,
  toNodeId,
  urlSafe,
} from "@/dashboard/lib/graph";
import type { GraphEdge, GraphNode, Neighbourhood } from "@/shared/types/api";

const TS = "2026-10-24T10:00:00+05:30";

function node(id: string, risk: number | null = null, flags: string[] = []): GraphNode {
  return { id, type: nodeType(id) ?? "Account", label: id, risk, flags };
}

function sent(source: string, target: string, amount: number, ts = TS): GraphEdge {
  return { source, target, type: "SENT", amount, ts };
}

describe("graph ids", () => {
  it("reads the type from the prefix", () => {
    expect(nodeType("A:U1029")).toBe("Account");
    expect(nodeType("P:refund.help@okbank")).toBe("Payee");
    expect(nodeType("D:88a1")).toBe("Device");
    expect(nodeType("I:103.21.44.10")).toBe("IP");
    expect(nodeType("X:1")).toBeUndefined();
    expect(nodeType("A:")).toBeUndefined();
  });

  it("turns a bare id into an account and keeps full graph ids", () => {
    expect(toNodeId(" U1029 ")).toBe("A:U1029");
    expect(toNodeId("P:refund.help@okbank")).toBe("P:refund.help@okbank");
    expect(toNodeId("")).toBeNull();
    expect(toNodeId("not an id!")).toBeNull();
  });

  it("only lets account ids into the URL", () => {
    expect(urlSafe("A:U1029")).toBe(true);
    expect(urlSafe("P:refund.help@okbank")).toBe(false);
    expect(urlSafe("I:103.21.44.10")).toBe(false);
  });
});

describe("mergeGraphs", () => {
  it("unions nodes, edges and rings, newer copies winning", () => {
    const a: Neighbourhood = {
      nodes: [node("A:1", 0.2), node("P:x", 0.1)],
      edges: [sent("A:1", "P:x", 100)],
      rings: [{ id: "r1", members: ["A:1"], score: 0.5 }],
      truncated: false,
    };
    const b: Neighbourhood = {
      nodes: [node("A:1", 0.9, ["gnn"]), node("A:2", 0.8)],
      edges: [sent("A:1", "P:x", 100), sent("A:2", "A:1", 50)],
      rings: [{ id: "r2", members: ["A:1", "A:2"], score: 0.9 }],
      truncated: true,
    };
    const m = mergeGraphs(a, b);
    expect(m.nodes.map((n) => n.id).sort()).toEqual(["A:1", "A:2", "P:x"]);
    expect(m.nodes.find((n) => n.id === "A:1")).toMatchObject({ risk: 0.9, flags: ["gnn"] });
    expect(m.edges).toHaveLength(2); // the shared edge once
    expect(m.rings.map((r) => r.id)).toEqual(["r2", "r1"]); // highest score first
    expect(m.truncated).toBe(true);
    expect(mergeGraphs(EMPTY_GRAPH, a)).toEqual({ ...a, rings: a.rings });
  });

  it("keeps two payments between the same pair at different times", () => {
    const m = mergeGraphs(
      { ...EMPTY_GRAPH, edges: [sent("A:1", "P:x", 100, TS)] },
      { ...EMPTY_GRAPH, edges: [sent("A:1", "P:x", 100, "2026-10-24T11:00:00+05:30")] },
    );
    expect(m.edges).toHaveLength(2);
  });
});

describe("rings", () => {
  const graph: Neighbourhood = {
    nodes: [node("A:1"), node("A:2"), node("A:3"), node("P:x")],
    edges: [sent("A:1", "A:2", 10), sent("A:2", "A:3", 10), sent("A:3", "P:x", 10)],
    rings: [
      { id: "low", members: ["A:2", "A:3"], score: 0.4 },
      { id: "high", members: ["A:1", "A:2"], score: 0.9 },
    ],
    truncated: false,
  };

  it("maps each member to its rings, best first", () => {
    const by = ringsByNode(graph.rings);
    expect(by.get("A:2")?.map((r) => r.id)).toEqual(["high", "low"]);
    expect(by.get("A:1")?.map((r) => r.id)).toEqual(["high"]);
    expect(by.has("P:x")).toBe(false);
  });

  it("marks only edges inside one ring", () => {
    const keys = ringEdgeKeys(graph);
    expect(keys.size).toBe(2); // A:1→A:2 and A:2→A:3, not A:3→P:x
    expect([...keys].some((k) => k.startsWith("A:3|P:x"))).toBe(false);
  });
});

describe("summarise", () => {
  it("counts payments in and out and other links", () => {
    const s = summarise("A:1", [
      sent("A:1", "P:x", 100),
      sent("A:1", "P:y", 50),
      sent("A:9", "A:1", 20),
      { source: "A:1", target: "D:1", type: "USED_DEVICE", amount: null, ts: TS },
      sent("A:8", "A:9", 999),
    ]);
    expect(s).toEqual({ sentCount: 2, sentAmount: 150, receivedCount: 1, receivedAmount: 20, linked: 1 });
  });
});

describe("byRisk", () => {
  it("puts the riskiest first and unscored nodes last", () => {
    const sorted = [node("D:1"), node("A:1", 0.2), node("A:2", 0.9)].sort(byRisk);
    expect(sorted.map((n) => n.id)).toEqual(["A:2", "A:1", "D:1"]);
  });
});

describe("placeAround", () => {
  it("places every new node on rings around the centre, none on top of it", () => {
    const points = placeAround({ x: 100, y: 50 }, 40);
    expect(points).toHaveLength(40);
    for (const p of points) expect(Math.hypot(p.x - 100, p.y - 50)).toBeGreaterThan(50);
    const unique = new Set(points.map((p) => `${Math.round(p.x)},${Math.round(p.y)}`));
    expect(unique.size).toBe(40);
  });
});
