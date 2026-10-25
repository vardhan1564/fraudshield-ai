import { describe, expect, it } from "vitest";

import { EMPTY_FEED, feedReducer, LIVE_LIMIT, mergeRows } from "@/dashboard/lib/liveFeed";
import type { TxnItem } from "@/shared/types/api";

const T0 = Date.parse("2026-10-21T10:00:00Z");

function row(n: number, risk = 0.01): TxnItem {
  return {
    id: `id-${n}`, txn_id: `TXN-${n}`, user_id: "u", customer_external_id: null, amount: 100 + n,
    currency: "INR", channel: "UPI", merchant_category: null, payee_vpa: "a@ok", risk_score: risk,
    decision: "APPROVE", action: "NONE", model_version: null, latency_ms: 10,
    ts: new Date(T0 + n * 1000).toISOString(), case_id: null,
  };
}

describe("live feed", () => {
  it("keeps the newest 200 payments, newest first", () => {
    const rows = mergeRows([], Array.from({ length: 250 }, (_, i) => row(i)));
    expect(rows).toHaveLength(LIVE_LIMIT);
    expect(rows[0]?.id).toBe("id-249");
    expect(rows.at(-1)?.id).toBe("id-50");
  });

  it("shows a payment once when both the socket and the poll bring it", () => {
    const first = mergeRows([row(1)], [row(2)]);
    const again = mergeRows(first, [{ ...row(2), risk_score: 0.9 }]);
    expect(again.map((r) => r.id)).toEqual(["id-2", "id-1"]);
    expect(again[0]?.risk_score).toBe(0.9);
  });

  it("holds new payments while paused and adds them on resume", () => {
    let feed = feedReducer(EMPTY_FEED, { type: "add", rows: [row(1)] });
    feed = feedReducer(feed, { type: "pause" });
    feed = feedReducer(feed, { type: "add", rows: [row(1), row(2), row(3)] });
    expect(feed.rows.map((r) => r.id)).toEqual(["id-1"]);
    expect(feed.pending.map((r) => r.id)).toEqual(["id-3", "id-2"]); // id-1 already shown
    feed = feedReducer(feed, { type: "resume" });
    expect(feed).toMatchObject({ paused: false, pending: [] });
    expect(feed.rows.map((r) => r.id)).toEqual(["id-3", "id-2", "id-1"]);
  });
});
