import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ToastContext, type ToastInput, type ToastTone } from "@/shared/hooks/toastContext";
import { cn } from "@/shared/lib/cn";

const SHOW_MS = 5_000;
const MAX_SHOWN = 3;

interface Toast extends ToastInput {
  id: number;
}

const LOOK: Record<ToastTone, { icon: typeof Info; className: string }> = {
  success: { icon: CircleCheck, className: "text-risk-low" },
  error: { icon: CircleAlert, className: "text-destructive" },
  info: { icon: Info, className: "text-primary" },
};

function ToastItem({ toast, onClose }: { toast: Toast; onClose: () => void }) {
  const { t } = useTranslation();
  const { icon: Icon, className } = LOOK[toast.tone ?? "info"];
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  }, [onClose]);
  useEffect(() => {
    const timer = setTimeout(() => close.current(), SHOW_MS);
    return () => clearTimeout(timer);
  }, []);
  return (
    <div
      role={toast.tone === "error" ? "alert" : "status"}
      className="animate-row-in pointer-events-auto flex w-80 max-w-[calc(100vw-2rem)] items-start gap-3 rounded-xl border bg-card p-3.5 text-sm shadow-lg"
    >
      <Icon className={cn("mt-0.5 size-4.5 shrink-0", className)} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">{toast.title}</p>
        {toast.body && <p className="mt-0.5 text-muted-foreground">{toast.body}</p>}
      </div>
      <button
        type="button"
        onClick={onClose}
        aria-label={t("common.close")}
        className="-m-1 cursor-pointer rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
      >
        <X className="size-4" aria-hidden="true" />
      </button>
    </div>
  );
}

/** Short confirmations (top on phones, bottom-right on wider screens); they close by themselves after 5 s. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(0);
  const remove = useCallback((id: number) => setToasts((all) => all.filter((x) => x.id !== id)), []);
  const toast = useCallback((input: ToastInput) => {
    next.current += 1;
    const id = next.current;
    setToasts((all) => [...all, { ...input, id }].slice(-MAX_SHOWN));
  }, []);
  const value = useMemo(() => ({ toast }), [toast]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* phones: top centre, clear of the bottom nav; wider screens: bottom right */}
      <div className="pointer-events-none fixed inset-x-4 top-4 z-50 flex flex-col items-center gap-2 sm:inset-x-auto sm:bottom-4 sm:right-4 sm:top-auto sm:items-end">
        {toasts.map((x) => (
          <ToastItem key={x.id} toast={x} onClose={() => remove(x.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}
