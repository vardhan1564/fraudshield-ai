import { Outlet } from "react-router-dom";

import { BottomNav } from "@/customer/layout/BottomNav";
import { SimContextProvider } from "@/customer/state/SimContextProvider";

/**
 * Spec 07 §1: on a laptop the app sits in a centred phone frame (max 420 px) so the demo can
 * show dashboard and phone side by side; on a real phone it fills the screen.
 */
export function PhoneFrame() {
  return (
    <SimContextProvider>
    <div className="min-h-dvh bg-background sm:grid sm:place-items-center sm:bg-muted sm:p-6">
      <div
        className={
          "relative mx-auto flex h-dvh w-full max-w-[420px] flex-col overflow-hidden bg-background " +
          "sm:h-[min(880px,calc(100dvh-3rem))] sm:rounded-[2.25rem] sm:border-[10px] " +
          "sm:border-foreground/90 sm:shadow-2xl dark:sm:border-black"
        }
      >
        {/* Notch, laptop only */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute left-1/2 top-0 z-20 hidden h-6 w-32 -translate-x-1/2 rounded-b-2xl bg-foreground/90 sm:block dark:bg-black"
        />
        <main className="flex-1 overflow-y-auto overscroll-contain text-base sm:pt-4">
          <Outlet />
        </main>
        <BottomNav />
      </div>
    </div>
    </SimContextProvider>
  );
}
