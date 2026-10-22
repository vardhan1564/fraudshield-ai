import { del, get, post, put } from "@/shared/api/client";
import type { Page, Rule, RuleIn } from "@/shared/types/api";

/** Rules CRUD (spec 04 endpoint 23, admin). A change reaches every API process on its next payment. */
export function listRules(params: { is_active?: boolean; page_size?: number } = {}): Promise<Page<Rule>> {
  return get<Page<Rule>>("/rules", { params: { page_size: 200, ...params } });
}

export function createRule(body: RuleIn): Promise<Rule> {
  return post<Rule>("/rules", body);
}

/** Full replace (PUT), as the API defines it. */
export function updateRule(id: string, body: RuleIn): Promise<Rule> {
  return put<Rule>(`/rules/${encodeURIComponent(id)}`, body);
}

export function deleteRule(id: string): Promise<void> {
  return del(`/rules/${encodeURIComponent(id)}`);
}
