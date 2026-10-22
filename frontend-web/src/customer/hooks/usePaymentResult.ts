import { useQuery } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import { type PaymentResult, recallPayment } from "@/customer/state/payments";
import { getTransaction } from "@/shared/api/endpoints/transactions";
import type { TxnDetail } from "@/shared/types/api";

function fromDetail(t: TxnDetail): PaymentResult | null {
  if (!t.decision) return null;
  return {
    txnId: t.txn_id,
    payee: t.payee_vpa,
    amount: t.amount,
    at: t.ts,
    decision: t.decision,
    action: (t.action ?? "NONE") as PaymentResult["action"],
    reasons: t.top_reasons,
    explanation: t.explanation ?? "",
    explanationPending: false,
  };
}

/**
 * The scored payment behind the warning / done screens: handed over by Pay, else kept for this
 * tab, else asked from the API (the customer's own payment, spec 04 endpoint 8).
 */
export function usePaymentResult(txnId: string): { result: PaymentResult | null; loading: boolean } {
  const location = useLocation();
  const handed = (location.state as PaymentResult | null) ?? null;
  const local = handed?.txnId === txnId ? handed : recallPayment(txnId);
  const remote = useQuery({
    queryKey: ["my-payment", txnId],
    queryFn: () => getTransaction(txnId),
    enabled: local === null,
    retry: false,
  });
  if (local) return { result: local, loading: false };
  return { result: remote.data ? fromDetail(remote.data) : null, loading: remote.isPending };
}
