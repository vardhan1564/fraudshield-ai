/**
 * Demo payment graph for the Graph Explorer mocks (T5.3), shaped like spec 04 §2.5. One mule ring
 * (8 mules + a cash-out account sharing two phones, paid by 60 victims and by the demo customers)
 * and a small new-account ring, among ordinary shoppers. Deterministic, so e2e checks are stable:
 * a depth-3 view of a mule reaches the 200-node cap.
 */
import type { GraphEdge, GraphNode, Neighbourhood, Page, RingItem } from "@/shared/types/api";

const NODES = new Map<string, GraphNode>();
const EDGES: GraphEdge[] = [];
const DAY = 86_400_000;
const NOW = Date.parse("2026-10-24T18:00:00+05:30");

let seed = 7;
function rand(): number {
  seed = (seed * 1_103_515_245 + 12_345) % 2 ** 31;
  return seed / 2 ** 31;
}

function account(ref: string, risk: number, flags: string[] = []): string {
  const id = `A:${ref}`;
  const digits = String(10 + Math.floor(rand() * 89));
  NODES.set(id, { id, type: "Account", label: `9${Math.floor(rand() * 9)}xxxxxx${digits}`, risk: Math.round(risk * 1000) / 1000, flags });
  return id;
}

function payee(vpa: string, risk: number): string {
  const id = `P:${vpa}`;
  const [name = "", bank = ""] = vpa.split("@");
  NODES.set(id, { id, type: "Payee", label: `${name.slice(0, 2)}***@${bank}`, risk, flags: [] });
  return id;
}

function device(ref: string): string {
  const id = `D:${ref}`;
  NODES.set(id, { id, type: "Device", label: `Device …${ref.slice(-4)}`, risk: null, flags: [] });
  return id;
}

function ip(octets: string): string {
  const id = `I:${octets}`;
  const [a = "", b = ""] = octets.split(".");
  NODES.set(id, { id, type: "IP", label: `${a}.${b}.x.x`, risk: null, flags: [] });
  return id;
}

function ts(daysAgo: number): string {
  return new Date(NOW - daysAgo * DAY).toISOString();
}

function sent(source: string, target: string, amount: number, daysAgo = rand() * 20): void {
  EDGES.push({ source, target, type: "SENT", amount: Math.round(amount), ts: ts(daysAgo) });
}

function link(type: "USED_DEVICE" | "LOGGED_FROM", source: string, target: string): void {
  EDGES.push({ source, target, type, amount: null, ts: ts(rand() * 25) });
}

/** One customer's own phone and network. */
function ownKit(acc: string, n: number): void {
  link("USED_DEVICE", acc, device(`d${(0x1000 + n).toString(16)}`));
  link("LOGGED_FROM", acc, ip(`103.${20 + (n % 30)}.${n % 250}.${(n * 7) % 250}`));
}

// shops ordinary people pay
const SHOPS = Array.from({ length: 20 }, (_, i) =>
  payee(`${["grocer", "fuel", "pharma", "kirana", "cafe"][i % 5]}.${i}@ok${["sbi", "hdfc", "axis", "icici"][i % 4]}`, Math.round(rand() * 8) / 100),
);
const SCAM_PAYEES = [payee("refund.helpdesk@ybl", 0.86), payee("cbi.verification@okaxis", 0.91)];

// the mule ring: mules share two phones and an IP, pass money on to one cash-out account
const MULE_FLAGS = ["mule_suspect", "gnn", "fan_in", "pass_through", "shared_device"];
export const MULES = Array.from({ length: 8 }, (_, i) =>
  account(`U090${i + 1}`, 0.86 + rand() * 0.12, i < 6 ? MULE_FLAGS : ["mule_suspect", "gnn", "shared_device"]),
);
const CASH_OUT = account("U0999", 0.93, ["mule_suspect", "gnn", "pass_through"]);
const MULE_PHONES = [device("mule-ph-7a21"), device("mule-ph-c3f9")];
const MULE_IP = ip("49.36.118.4");
MULES.forEach((m, i) => {
  link("USED_DEVICE", m, MULE_PHONES[i % 2] ?? "");
  link("LOGGED_FROM", m, MULE_IP);
  sent(m, CASH_OUT, 40_000 + rand() * 90_000, rand() * 3);
});
link("USED_DEVICE", CASH_OUT, MULE_PHONES[0] ?? "");

// victims pay one mule each (victim i pays mule i % 8), then go on with normal life
for (let i = 0; i < 60; i += 1) {
  const v = account(`U04${String(i).padStart(2, "0")}`, 0.02 + rand() * 0.18);
  ownKit(v, i);
  sent(v, MULES[i % MULES.length] ?? "", 8_000 + rand() * 60_000, rand() * 6);
  for (let k = 0; k < 1 + (i % 3); k += 1) sent(v, SHOPS[(i * 3 + k) % SHOPS.length] ?? "", 150 + rand() * 4_000);
}

// the demo customers (Case Detail's payments) were caught paying the scam payees and two mules
for (let i = 0; i < 7; i += 1) {
  const c = account(`U0${1029 + i}`, 0.05 + rand() * 0.2);
  ownKit(c, 100 + i);
  sent(c, SCAM_PAYEES[i % 2] ?? "", 20_000 + rand() * 60_000, rand() * 2);
  if (i < 3) sent(c, MULES[i] ?? "", 15_000 + rand() * 30_000, rand() * 2);
  sent(c, SHOPS[i] ?? "", 300 + rand() * 2_000);
}

// ordinary shoppers
for (let i = 0; i < 110; i += 1) {
  const a = account(`U05${String(i).padStart(2, "0")}`, rand() * 0.12);
  ownKit(a, 200 + i);
  for (let k = 0; k < 2; k += 1) sent(a, SHOPS[(i + k * 7) % SHOPS.length] ?? "", 100 + rand() * 3_000);
}

// a second, weaker ring: four new accounts on one phone paying each other
const BURST = Array.from({ length: 4 }, (_, i) => account(`U070${i}`, 0.55 + rand() * 0.1, ["new_account_burst", "shared_device"]));
const BURST_PHONE = device("burst-ph-9e02");
BURST.forEach((a, i) => {
  link("USED_DEVICE", a, BURST_PHONE);
  sent(a, BURST[(i + 1) % BURST.length] ?? "", 2_000 + rand() * 5_000, rand() * 4);
});
sent(BURST[0] ?? "", SHOPS[3] ?? "", 999);

export const RINGS: RingItem[] = [
  {
    id: "00000000-0000-4000-a000-00000000e001",
    members: [...MULES, CASH_OUT],
    size: MULES.length + 1,
    score: 0.94,
    detector: "gnn",
    status: "suspected",
    first_seen: ts(9),
    last_seen: ts(0.2),
  },
  {
    id: "00000000-0000-4000-a000-00000000e002",
    members: BURST,
    size: BURST.length,
    score: 0.62,
    detector: "new_account_burst",
    status: "suspected",
    first_seen: ts(3),
    last_seen: ts(0.5),
  },
];

const MAX_NODES = 200;

/** GET /graph/entity/{id}?depth= on the demo graph: breadth-first, at most 200 nodes. */
export function neighbourhood(id: string, depth: number): Neighbourhood | null {
  if (!NODES.has(id)) return null;
  const visited = new Set([id]);
  let frontier = new Set([id]);
  let truncated = false;
  for (let hop = 0; hop < depth && frontier.size; hop += 1) {
    const next = new Set<string>();
    for (const e of EDGES) {
      const ends = frontier.has(e.source) ? e.target : frontier.has(e.target) ? e.source : null;
      if (ends === null || visited.has(ends)) continue;
      if (visited.size >= MAX_NODES) {
        truncated = true;
        continue;
      }
      visited.add(ends);
      next.add(ends);
    }
    frontier = next;
  }
  const nodes = [...visited].map((n) => NODES.get(n)).filter((n): n is GraphNode => n !== undefined);
  const edges = EDGES.filter((e) => visited.has(e.source) && visited.has(e.target));
  const rings = RINGS.filter((r) => r.status !== "dismissed" && r.members.some((m) => visited.has(m))).map(({ id: ringId, members, score }) => ({
    id: ringId,
    members,
    score,
  }));
  return { nodes, edges, rings, truncated };
}

export function ringPage(params: URLSearchParams): Page<RingItem> {
  const status = params.get("status");
  const minScore = Number(params.get("min_score") ?? "0");
  const items = RINGS.filter((r) => (!status || r.status === status) && r.score >= minScore);
  return { items, page: 1, page_size: Number(params.get("page_size") ?? "50"), total: items.length };
}
