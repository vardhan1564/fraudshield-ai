import { get } from "@/shared/api/client";
import type { Neighbourhood, Page, RingFilters, RingItem } from "@/shared/types/api";

/** A node and everything within `depth` hops (1-3) over the last 30 days, at most 200 nodes. */
export function graphEntity(id: string, depth = 2): Promise<Neighbourhood> {
  return get<Neighbourhood>(`/graph/entity/${encodeURIComponent(id)}`, { params: { depth } });
}

/** Suspected mule rings, highest score first. */
export function listRings(filters: RingFilters = {}): Promise<Page<RingItem>> {
  return get<Page<RingItem>>("/graph/rings", { params: filters });
}
