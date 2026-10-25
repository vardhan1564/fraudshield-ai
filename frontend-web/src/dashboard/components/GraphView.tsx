import cytoscape from "cytoscape";
import { useEffect, useRef } from "react";

import { edgeKey, placeAround, ringEdgeKeys, ringsByNode } from "@/dashboard/lib/graph";
import { useTheme } from "@/shared/hooks/useTheme";
import { riskLevel } from "@/shared/lib/riskColor";
import type { GraphNodeType, Neighbourhood } from "@/shared/types/api";

const SHAPES: Record<GraphNodeType, cytoscape.Css.NodeShape> = {
  Account: "ellipse",
  Payee: "round-rectangle",
  Device: "diamond",
  IP: "hexagon",
};

/** Cytoscape can't parse OKLCH, so the theme tokens are resolved to rgb() through a canvas. */
function resolveColours(names: readonly string[]): Record<string, string> {
  const ctx = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  const root = getComputedStyle(document.documentElement);
  const out: Record<string, string> = {};
  for (const name of names) {
    const css = root.getPropertyValue(`--${name}`).trim() || "#888";
    if (!ctx) {
      out[name] = css;
      continue;
    }
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = css;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
    out[name] = `rgb(${r ?? 136}, ${g ?? 136}, ${b ?? 136})`;
  }
  return out;
}

const TOKENS = ["risk-low", "risk-medium", "risk-high", "muted-foreground", "foreground", "border", "card", "graph-ring", "primary"] as const;

function stylesheet(): cytoscape.StylesheetJson {
  const c = resolveColours(TOKENS);
  return [
    {
      selector: "node",
      style: {
        width: 22,
        height: 22,
        shape: "data(shape)" as unknown as cytoscape.Css.NodeShape,
        "background-color": c["muted-foreground"],
        "border-width": 1.5,
        "border-color": c.card,
        label: "data(label)",
        "font-size": 9,
        "font-family": "Inter, system-ui, sans-serif",
        color: c.foreground,
        "text-valign": "bottom",
        "text-margin-y": 3,
        "text-outline-color": c.card,
        "text-outline-width": 2,
        "min-zoomed-font-size": 8, // labels disappear when zoomed out: fast and uncluttered
      },
    },
    { selector: "node[band = 'low']", style: { "background-color": c["risk-low"] } },
    { selector: "node[band = 'medium']", style: { "background-color": c["risk-medium"] } },
    { selector: "node[band = 'high']", style: { "background-color": c["risk-high"] } },
    {
      selector: "node.ring",
      style: { "underlay-color": c["graph-ring"], "underlay-opacity": 0.6, "underlay-padding": 8, "underlay-shape": "ellipse", "z-index": 5 },
    },
    { selector: "node.focus", style: { width: 32, height: 32, "font-weight": 600 } },
    {
      selector: "node.selected",
      style: { "border-width": 3.5, "border-color": c.foreground, "z-index": 10 },
    },
    {
      selector: "edge",
      style: {
        width: 1.2,
        "line-color": c["muted-foreground"],
        "line-opacity": 0.45,
        "curve-style": "haystack", // the cheapest edge to draw
      },
    },
    {
      selector: "edge[type = 'SENT']",
      style: {
        "curve-style": "straight",
        "target-arrow-shape": "triangle",
        "target-arrow-color": c["muted-foreground"],
        "arrow-scale": 0.7,
        width: 1.5,
      },
    },
    { selector: "edge[type != 'SENT']", style: { "line-style": "dashed" } },
    {
      selector: "edge.ring",
      style: { "line-color": c["graph-ring"], "target-arrow-color": c["graph-ring"], "line-opacity": 0.95, width: 3, "z-index": 4 },
    },
  ];
}

function nodeData(n: Neighbourhood["nodes"][number]) {
  return {
    id: n.id,
    label: n.label,
    shape: SHAPES[n.type],
    band: n.risk === null ? "none" : riskLevel(n.risk),
  };
}

export interface GraphHandle {
  nodeCount: () => number;
  ringNodeCount: () => number;
  renderedPosition: (id: string) => { x: number; y: number } | null;
}

/**
 * The Cytoscape canvas of the Graph Explorer (spec 06 §3): node colour = risk band, shape = node
 * type, ring members get a violet halo and ring edges are violet. A new `graphKey` starts a fresh
 * picture with a force layout; within one key, newly loaded nodes are placed around `origin` (the
 * node that was clicked) so the rest of the picture stays still.
 */
export function GraphView({
  graph,
  graphKey,
  focusId,
  selectedId,
  origin,
  onNodeClick,
  label,
}: {
  graph: Neighbourhood;
  graphKey: string;
  focusId: string;
  selectedId: string | null;
  origin: string | null;
  onNodeClick: (id: string) => void;
  label: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const keyRef = useRef<string | null>(null);
  const clickRef = useRef(onNodeClick);
  const { theme } = useTheme();

  useEffect(() => {
    clickRef.current = onNodeClick;
  }, [onNodeClick]);

  // One Cytoscape instance for the life of the page.
  useEffect(() => {
    if (!container.current) return;
    const cy = cytoscape({
      container: container.current,
      style: stylesheet(),
      minZoom: 0.15,
      maxZoom: 3,
      textureOnViewport: true, // pan/zoom moves a bitmap: smooth with hundreds of nodes
      hideEdgesOnViewport: true,
      boxSelectionEnabled: false,
    });
    cy.on("tap", "node", (e) => clickRef.current(e.target.id()));
    cyRef.current = cy;
    // Cytoscape caches where its canvas is; when a notice above it appears or goes, clicks would
    // land on the wrong node unless it is told the container moved.
    const observer = new ResizeObserver(() => cy.resize());
    observer.observe(container.current);
    if (import.meta.env.MODE === "mock") {
      // e2e hook on the demo build only: counts and on-screen positions for the done-when checks
      const handle: GraphHandle = {
        nodeCount: () => cy.nodes().length,
        ringNodeCount: () => cy.nodes(".ring").length,
        renderedPosition: (id) => {
          const n = cy.getElementById(id);
          return n.nonempty() ? n.renderedPosition() : null;
        },
      };
      (window as unknown as { __graph?: GraphHandle }).__graph = handle;
    }
    return () => {
      observer.disconnect();
      cy.destroy();
      cyRef.current = null;
      keyRef.current = null;
    };
  }, []);

  // Theme switch: re-resolve the colour tokens.
  useEffect(() => {
    cyRef.current?.style(stylesheet());
  }, [theme]);

  // Sync the elements with the merged neighbourhood.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const fresh = keyRef.current !== graphKey;
    keyRef.current = graphKey;
    const ringNodes = ringsByNode(graph.rings);
    const ringEdges = ringEdgeKeys(graph);

    cy.batch(() => {
      if (fresh) cy.elements().remove();
      const center = origin && !fresh ? cy.getElementById(origin) : null;
      const added = graph.nodes.filter((n) => cy.getElementById(n.id).empty());
      const spots = center?.nonempty() ? placeAround(center.position(), added.length) : [];
      added.forEach((n, i) => {
        cy.add({ group: "nodes", data: nodeData(n), position: spots[i] ?? { x: 0, y: 0 } });
      });
      for (const n of graph.nodes) {
        const el = cy.getElementById(n.id);
        el.data(nodeData(n));
        el.toggleClass("ring", ringNodes.has(n.id));
        el.toggleClass("focus", n.id === focusId);
        el.toggleClass("selected", n.id === selectedId);
      }
      for (const e of graph.edges) {
        const id = edgeKey(e);
        if (cy.getElementById(id).empty()) {
          cy.add({ group: "edges", data: { id, source: e.source, target: e.target, type: e.type } });
        }
        cy.getElementById(id).toggleClass("ring", ringEdges.has(id));
      }
    });

    if (fresh && graph.nodes.length) {
      cy.layout({
        name: "cose",
        animate: false,
        randomize: true,
        nodeRepulsion: () => 400_000, // dense rings (shared phones, one cash-out) need room
        idealEdgeLength: () => 70,
        nodeOverlap: 24,
        gravity: 0.4,
        numIter: 200, // ~0.8 s for 200 nodes; more iterations barely change the picture
        fit: true,
        padding: 24,
      }).run();
    } else if (origin) {
      const center = cy.getElementById(origin);
      if (center.nonempty()) cy.animate({ center: { eles: center } }, { duration: 250 });
    }
  }, [graph, graphKey, focusId, selectedId, origin]);

  return <div ref={container} role="img" aria-label={label} className="size-full" data-testid="graph-canvas" />;
}
