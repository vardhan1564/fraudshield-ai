import type { SocketStatus } from "@/shared/api/ws";
import { useWebSocket } from "@/shared/hooks/useWebSocket";
import type { CustomerMessage } from "@/shared/types/api";

/** `/ws/customer` (spec 04 §3): messages for the signed-in customer only, e.g. explanation.ready. */
export function useCustomerSocket(onMessage: (message: CustomerMessage) => void): SocketStatus {
  return useWebSocket<CustomerMessage>("/customer", onMessage);
}
