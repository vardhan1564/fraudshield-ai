import { useTranslation } from "react-i18next";

import type { SocketStatus } from "@/shared/api/ws";
import { cn } from "@/shared/lib/cn";

const DOT: Record<SocketStatus, string> = {
  open: "bg-risk-low",
  connecting: "bg-risk-medium animate-pulse",
  reconnecting: "bg-risk-medium animate-pulse",
  closed: "bg-muted-foreground",
};

/** Whether the live feed is connected, in words as well as colour. */
export function ConnectionStatus({ status, hint }: { status: SocketStatus; hint?: string }) {
  const { t } = useTranslation();
  return (
    <span
      role="status"
      title={hint}
      className="inline-flex h-8 items-center gap-2 rounded-full border bg-card px-3 text-[13px] font-medium"
    >
      <span className={cn("size-2 rounded-full", DOT[status])} aria-hidden="true" />
      {t(`connection.${status}`)}
    </span>
  );
}
