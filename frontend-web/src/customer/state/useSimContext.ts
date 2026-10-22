import { useContext } from "react";

import { SimContextContext, type SimContextValue } from "@/customer/state/simContext";

export function useSimContext(): SimContextValue {
  const ctx = useContext(SimContextContext);
  if (!ctx) throw new Error("useSimContext must be used inside <SimContextProvider>");
  return ctx;
}
