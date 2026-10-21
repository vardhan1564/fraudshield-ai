/**
 * The Live Monitor's rows (spec 06 §3): newest first, one row per payment, at most 200.
 * While paused, new payments wait in `pending` and the visible rows stay still.
 */
import type { TxnItem } from "@/shared/types/api";

export const LIVE_LIMIT = 200;

export interface LiveFeed {
  rows: TxnItem[];
  pending: TxnItem[];
  paused: boolean;
}

export const EMPTY_FEED: LiveFeed = { rows: [], pending: [], paused: false };

/** Newest first by payment time; a payment seen twice (WebSocket and poll) keeps its latest copy. */
export function mergeRows(current: TxnItem[], incoming: TxnItem[], limit = LIVE_LIMIT): TxnItem[] {
  const byId = new Map(current.map((r) => [r.id, r]));
  for (const r of incoming) byId.set(r.id, r);
  return [...byId.values()].sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts)).slice(0, limit);
}

export type FeedAction =
  | { type: "add"; rows: TxnItem[] }
  | { type: "pause" }
  | { type: "resume" }
  | { type: "clear" };

export function feedReducer(feed: LiveFeed, action: FeedAction): LiveFeed {
  switch (action.type) {
    case "add": {
      if (action.rows.length === 0) return feed;
      if (!feed.paused) return { ...feed, rows: mergeRows(feed.rows, action.rows) };
      const shown = new Set(feed.rows.map((r) => r.id));
      const fresh = action.rows.filter((r) => !shown.has(r.id));
      return { ...feed, pending: mergeRows(feed.pending, fresh) };
    }
    case "pause":
      return { ...feed, paused: true };
    case "resume":
      return { rows: mergeRows(feed.rows, feed.pending), pending: [], paused: false };
    case "clear":
      return { ...feed, rows: [], pending: [] };
  }
}
