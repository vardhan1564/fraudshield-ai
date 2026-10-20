import { get, post } from "@/shared/api/client";
import type { PayeeReputation, ReportCreate, ReportCreated } from "@/shared/types/api";

export function payeeReputation(vpa: string): Promise<PayeeReputation> {
  return get<PayeeReputation>(`/payees/${encodeURIComponent(vpa.trim().toLowerCase())}/reputation`);
}

export function reportPayee(body: ReportCreate): Promise<ReportCreated> {
  return post<ReportCreated>("/reports", body);
}
