import { useEffect, useState } from "react";

/** The current time in ms, re-rendering every `everyMs` (timers, "waiting for" columns). */
export function useNow(everyMs = 1_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(id);
  }, [everyMs]);
  return now;
}
