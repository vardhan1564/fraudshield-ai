import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import type { SocketStatus } from "@/shared/api/ws";
import { useWebSocket } from "@/shared/hooks/useWebSocket";
import type { AlertCreatedMessage, AnalystMessage, ExplanationReadyMessage } from "@/shared/types/api";

export const ALERTS_KEY = ["alerts"] as const;
/** Everything under ALERTS_KEY refetches on alert.created / case.updated. */
export const QUEUE_KEY = [...ALERTS_KEY, "queue"] as const;
export const caseKey = (caseId: string) => [...ALERTS_KEY, "case", caseId] as const;

/**
 * The analyst feed `/ws/alerts` (spec 04 §3), reconnecting by itself. A new alert or a case
 * change marks the alert queue stale so every open queue refetches; `onAlert` gets each new HOLD
 * and `onExplanation` each LLM sentence the worker finishes.
 */
export function useAlertStream(
  onAlert?: (alert: AlertCreatedMessage["data"]) => void,
  onExplanation?: (ready: ExplanationReadyMessage["data"]) => void,
): SocketStatus {
  const queryClient = useQueryClient();
  const handle = useCallback(
    (message: AnalystMessage) => {
      if (message.type === "alert.created") onAlert?.(message.data);
      if (message.type === "explanation.ready") onExplanation?.(message.data);
      if (message.type === "alert.created" || message.type === "case.updated") {
        void queryClient.invalidateQueries({ queryKey: ALERTS_KEY });
      }
    },
    [onAlert, onExplanation, queryClient],
  );
  return useWebSocket<AnalystMessage>("/alerts", handle);
}
