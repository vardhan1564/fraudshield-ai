import { refreshAccessToken } from "@/shared/api/client";
import { tokens } from "@/shared/api/tokens";

export const WS_URL: string = import.meta.env.VITE_WS_URL ?? "ws://localhost:8000/api/v1/ws";

export type SocketStatus = "connecting" | "open" | "reconnecting" | "closed";

const POLICY_VIOLATION = 1008; // bad or expired token (spec 04 §3)
const MIN_DELAY_MS = 1_000;
const MAX_DELAY_MS = 30_000;

interface Options<M> {
  onMessage: (message: M) => void;
  onStatus?: (status: SocketStatus) => void;
}

/**
 * A WebSocket to `/ws/alerts` or `/ws/customer` that stays connected: it reconnects with
 * exponential back-off (1 s → 30 s, with jitter) and gets a fresh token first when the server
 * closed it for an expired one (1008). `close()` stops it for good.
 */
export class ReconnectingSocket<M> {
  private socket: WebSocket | null = null;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private needsFreshToken = false;

  constructor(
    private readonly path: "/alerts" | "/customer",
    private readonly options: Options<M>,
  ) {}

  start(): void {
    this.stopped = false;
    void this.connect();
  }

  close(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.socket?.close(1000);
    this.socket = null;
    this.options.onStatus?.("closed");
  }

  private async connect(): Promise<void> {
    if (this.stopped) return;
    this.options.onStatus?.(this.attempt === 0 ? "connecting" : "reconnecting");
    let token = tokens.access;
    try {
      if (this.needsFreshToken || tokens.expiresSoon()) token = await refreshAccessToken();
      this.needsFreshToken = false;
    } catch {
      this.scheduleReconnect();
      return;
    }
    if (!token || this.stopped) return;

    const socket = new WebSocket(`${WS_URL}${this.path}?token=${encodeURIComponent(token)}`);
    this.socket = socket;
    socket.onopen = () => {
      this.attempt = 0;
      this.options.onStatus?.("open");
    };
    socket.onmessage = (event: MessageEvent<string>) => {
      try {
        this.options.onMessage(JSON.parse(event.data) as M);
      } catch {
        // not JSON: ignore
      }
    };
    socket.onclose = (event: CloseEvent) => {
      if (this.socket !== socket || this.stopped) return;
      this.socket = null;
      if (event.code === POLICY_VIOLATION) this.needsFreshToken = true; // expired token
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    this.options.onStatus?.("reconnecting");
    const base = Math.min(MAX_DELAY_MS, MIN_DELAY_MS * 2 ** this.attempt);
    const delay = base / 2 + Math.random() * (base / 2);
    this.attempt += 1;
    this.timer = setTimeout(() => void this.connect(), delay);
  }
}
