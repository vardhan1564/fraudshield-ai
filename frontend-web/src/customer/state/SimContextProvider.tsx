import { type ReactNode, useCallback, useMemo, useState } from "react";

import { loadSim, saveSim, type SimContext, SimContextContext } from "@/customer/state/simContext";

/** Holds the simulated phone signals for the whole customer app; remembered across reloads. */
export function SimContextProvider({ children }: { children: ReactNode }) {
  const [sim, setSim] = useState<SimContext>(loadSim);
  const set = useCallback((next: SimContext) => {
    saveSim(next);
    setSim(next);
  }, []);
  const update = useCallback(
    (change: Partial<SimContext>) =>
      setSim((current) => {
        const next = { ...current, ...change };
        saveSim(next);
        return next;
      }),
    [],
  );
  const value = useMemo(() => ({ sim, update, set }), [sim, update, set]);
  return <SimContextContext.Provider value={value}>{children}</SimContextContext.Provider>;
}
