import { useEffect, useRef, useState } from "react";

import { ReconnectingSocket, type SocketStatus } from "@/shared/api/ws";
import { useAuth } from "@/shared/hooks/useAuth";

/**
 * Subscribe to `/ws/alerts` (analysts) or `/ws/customer` while signed in. The socket reconnects
 * by itself; `onMessage` always sees the latest render's closure.
 */
export function useWebSocket<M>(
  path: "/alerts" | "/customer",
  onMessage: (message: M) => void,
): SocketStatus {
  const { state } = useAuth();
  const [status, setStatus] = useState<SocketStatus>("closed");
  const handler = useRef(onMessage);

  useEffect(() => {
    handler.current = onMessage;
  }, [onMessage]);

  const signedIn = state.status === "authenticated";
  useEffect(() => {
    if (!signedIn) return;
    const socket = new ReconnectingSocket<M>(path, {
      onMessage: (m) => handler.current(m),
      onStatus: setStatus,
    });
    socket.start();
    return () => socket.close();
  }, [path, signedIn]);

  return status;
}
