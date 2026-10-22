import { useContext } from "react";

import { ToastContext, type ToastContextValue } from "@/shared/hooks/toastContext";

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}
