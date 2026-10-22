import { LogOut, Menu, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { ADMIN_NAV, ALL_NAV, MAIN_NAV, type NavItem } from "@/dashboard/layout/nav";
import { LanguageSwitcher } from "@/shared/components/LanguageSwitcher";
import { Logo } from "@/shared/components/Logo";
import { ThemeToggle } from "@/shared/components/ThemeToggle";
import { Badge } from "@/shared/components/ui/badge";
import { Button } from "@/shared/components/ui/button";
import { useAuth } from "@/shared/hooks/useAuth";
import { cn } from "@/shared/lib/cn";
import { IS_MOCK } from "@/shared/lib/demo";
import type { Me } from "@/shared/types/api";

function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}

function NavSection({ title, items, onNavigate }: { title: string; items: NavItem[]; onNavigate: () => void }) {
  const { t } = useTranslation();
  if (items.length === 0) return null;
  return (
    <div>
      <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/80">
        {title}
      </p>
      <ul className="space-y-0.5">
        {items.map(({ to, labelKey, icon: Icon }) => (
          <li key={to}>
            <NavLink
              to={to}
              end={to === "/dashboard"}
              onClick={onNavigate}
              className={({ isActive }) =>
                cn(
                  "flex h-10 items-center gap-3 rounded-lg px-3 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-secondary text-secondary-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )
              }
            >
              <Icon className="size-[18px] shrink-0" aria-hidden="true" />
              {t(labelKey)}
            </NavLink>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Sidebar({ user, onNavigate }: { user: Me; onNavigate: () => void }) {
  const { t } = useTranslation();
  const { logout } = useAuth();
  const allowed = (items: NavItem[]) => items.filter((i) => i.roles.includes(user.role));
  return (
    <div className="flex h-full flex-col bg-sidebar">
      <div className="flex h-16 items-center px-5">
        <Logo />
      </div>
      <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-4" aria-label="Dashboard">
        <NavSection title={t("nav.main")} items={allowed(MAIN_NAV)} onNavigate={onNavigate} />
        <NavSection title={t("nav.admin")} items={allowed(ADMIN_NAV)} onNavigate={onNavigate} />
      </nav>
      <div className="border-t p-3">
        <div className="flex items-center gap-3 rounded-lg px-2 py-2">
          <span className="grid size-9 shrink-0 place-items-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">
            {initials(user.name)}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{user.name}</p>
            <p className="truncate text-xs text-muted-foreground">{t(`roles.${user.role}`)}</p>
          </div>
          <Button variant="ghost" size="icon" onClick={logout} aria-label={t("common.logout")} title={t("common.logout")}>
            <LogOut aria-hidden="true" />
          </Button>
        </div>
      </div>
    </div>
  );
}

/** Desktop: fixed sidebar. Below `lg`: the sidebar is a drawer behind a menu button. */
export function DashboardLayout() {
  const { t } = useTranslation();
  const { state } = useAuth();
  const location = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawerOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  if (state.status !== "authenticated") return null; // RequireAuth handles this
  const user = state.user;
  const current =
    ALL_NAV.find((n) => n.to === location.pathname) ??
    ALL_NAV.find((n) => n.to !== "/dashboard" && location.pathname.startsWith(n.to));

  return (
    <div className="min-h-dvh bg-background">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r lg:block">
        <Sidebar user={user} onNavigate={() => undefined} />
      </aside>

      {/* Mobile / tablet drawer */}
      <div
        className={cn(
          "fixed inset-0 z-40 bg-black/40 backdrop-blur-[2px] transition-opacity lg:hidden",
          drawerOpen ? "opacity-100" : "pointer-events-none opacity-0",
        )}
        onClick={() => setDrawerOpen(false)}
        aria-hidden="true"
      />
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 w-72 max-w-[85vw] border-r shadow-xl transition-[translate,visibility] duration-200 lg:hidden",
          drawerOpen ? "visible translate-x-0" : "invisible -translate-x-full", // invisible: not tabbable
        )}
        aria-hidden={!drawerOpen}
      >
        <Sidebar user={user} onNavigate={() => setDrawerOpen(false)} />
        <Button
          variant="ghost"
          size="icon"
          className="absolute right-2 top-3"
          onClick={() => setDrawerOpen(false)}
          aria-label={t("common.closeMenu")}
        >
          <X aria-hidden="true" />
        </Button>
      </aside>

      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur-md sm:px-6">
          <Button
            variant="ghost"
            size="icon"
            className="-ml-2 lg:hidden"
            onClick={() => setDrawerOpen(true)}
            aria-label={t("common.openMenu")}
          >
            <Menu aria-hidden="true" />
          </Button>
          <h1 className="min-w-0 flex-1 truncate text-base font-semibold sm:text-lg">
            {current
              ? t(current.labelKey)
              : location.pathname.startsWith("/dashboard/cases/")
                ? t("case.title")
                : t("app.name")}
          </h1>
          {IS_MOCK && (
            <Badge variant="muted" className="hidden sm:inline-flex">
              {t("common.mockMode")}
            </Badge>
          )}
          <LanguageSwitcher className="hidden sm:inline-flex" />
          <ThemeToggle />
        </header>
        <main className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
