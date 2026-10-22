import { useTranslation } from "react-i18next";
import { NavLink } from "react-router-dom";

import { CUSTOMER_NAV } from "@/customer/layout/customerNav";
import { cn } from "@/shared/lib/cn";

/** Icons always with labels; ≥ 44 px tap targets (spec 07 §4). */
export function BottomNav() {
  const { t } = useTranslation();
  return (
    <nav
      aria-label="App"
      className="border-t bg-card/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md"
    >
      <ul className="grid grid-cols-5">
        {CUSTOMER_NAV.map(({ to, labelKey, icon: Icon }) => (
          <li key={to}>
            <NavLink
              to={to}
              end={to === "/app"}
              className={({ isActive }) =>
                cn(
                  "flex min-h-16 flex-col items-center justify-center gap-1 px-0.5 text-base font-medium leading-tight transition-colors",
                  isActive ? "text-primary" : "text-muted-foreground hover:text-foreground",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <span
                    className={cn(
                      "grid h-7 w-12 place-items-center rounded-full transition-colors",
                      isActive && "bg-secondary",
                    )}
                  >
                    <Icon className="size-[22px]" aria-hidden="true" />
                  </span>
                  <span className="max-w-full truncate">{t(labelKey)}</span>
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
