import { get, post } from "@/shared/api/client";
import type {
  ExplainResponse,
  Language,
  Page,
  ScoreRequest,
  ScoreResponse,
  TxnDetail,
  TxnFilters,
  TxnItem,
} from "@/shared/types/api";

export function scorePayment(body: ScoreRequest): Promise<ScoreResponse> {
  return post<ScoreResponse>("/transactions/score", body);
}

export function listTransactions(filters: TxnFilters = {}): Promise<Page<TxnItem>> {
  return get<Page<TxnItem>>("/transactions", { params: filters });
}

/** By the client's txn_id or the transaction UUID. */
export function getTransaction(ref: string): Promise<TxnDetail> {
  return get<TxnDetail>(`/transactions/${encodeURIComponent(ref)}`);
}

export function explainTransaction(ref: string, lang?: Language): Promise<ExplainResponse> {
  return get<ExplainResponse>(`/transactions/${encodeURIComponent(ref)}/explain`, {
    params: lang ? { lang } : undefined,
  });
}
