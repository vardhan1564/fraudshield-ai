import { get, patch } from "@/shared/api/client";
import type { AlertItem, AlertStatus, CaseDetail, CaseUpdate, Page } from "@/shared/types/api";

export function listAlerts(
  params: { status?: AlertStatus; page?: number; page_size?: number } = {},
): Promise<Page<AlertItem>> {
  return get<Page<AlertItem>>("/alerts", { params });
}

export function getCase(caseId: string): Promise<CaseDetail> {
  return get<CaseDetail>(`/cases/${encodeURIComponent(caseId)}`);
}

export function updateCase(caseId: string, body: CaseUpdate): Promise<CaseDetail> {
  return patch<CaseDetail>(`/cases/${encodeURIComponent(caseId)}`, body);
}
