import { api, get } from "@/shared/api/client";
import type { AuditEntry, IsoDateTime, Page } from "@/shared/types/api";

/** What the API writes (spec 04 §1.1, T3.7, T4.5). */
export const AUDIT_ENTITIES = ["case", "rule", "user", "report", "model_version"] as const;
export const AUDIT_ACTIONS = [
  "case.update", "rule.create", "rule.update", "rule.delete", "model.deploy", "report.create",
  "user.update", "auth.register", "auth.login", "auth.login_failed",
] as const;

export interface AuditFilters {
  entity?: string;
  actor?: string;
  action?: string;
  from?: IsoDateTime;
  to?: IsoDateTime;
  page?: number;
  page_size?: number;
}

/** Audit entries, newest first (spec 04 endpoint 27, admin). */
export function listAudit(filters: AuditFilters = {}): Promise<Page<AuditEntry>> {
  return get<Page<AuditEntry>>("/audit-log", { params: filters });
}

/**
 * The same filters as a CSV file (`?format=csv`, up to 50,000 rows). Fetched with the bearer
 * token like any call, never a token in the URL (CLAUDE.md rule 8), then saved by the browser.
 */
export async function downloadAuditCsv(filters: AuditFilters = {}): Promise<void> {
  const { entity, actor, action, from, to } = filters; // every match, not one page
  const response = await api.get<string>("/audit-log", {
    params: { entity, actor, action, from, to, format: "csv" },
    responseType: "text", // CSV is text; the file is made here, so no binary handling is needed
  });
  const url = URL.createObjectURL(new Blob([response.data], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1_000);
}
