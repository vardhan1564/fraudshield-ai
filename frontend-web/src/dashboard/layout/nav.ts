import {
  Activity,
  Bell,
  LayoutDashboard,
  type LucideIcon,
  Network,
  ScrollText,
  SlidersHorizontal,
  Store,
  Brain,
} from "lucide-react";

import type { Role } from "@/shared/types/api";

export interface NavItem {
  to: string;
  labelKey:
    | "nav.overview" | "nav.live" | "nav.alerts" | "nav.graph" | "nav.payees"
    | "nav.rules" | "nav.models" | "nav.audit";
  icon: LucideIcon;
  roles: Role[];
  task: string; // the plan task that builds the page
}

const STAFF: Role[] = ["analyst", "admin"];

/** Spec 06 §3 pages. Admin pages are hidden from analysts. */
export const MAIN_NAV: NavItem[] = [
  { to: "/dashboard", labelKey: "nav.overview", icon: LayoutDashboard, roles: STAFF, task: "T4.2" },
  { to: "/dashboard/live", labelKey: "nav.live", icon: Activity, roles: ["analyst"], task: "T4.3" },
  { to: "/dashboard/alerts", labelKey: "nav.alerts", icon: Bell, roles: ["analyst"], task: "T4.3" },
  { to: "/dashboard/graph", labelKey: "nav.graph", icon: Network, roles: ["analyst"], task: "T5.3" },
  { to: "/dashboard/payees", labelKey: "nav.payees", icon: Store, roles: ["analyst"], task: "T4.7" },
];

export const ADMIN_NAV: NavItem[] = [
  { to: "/dashboard/rules", labelKey: "nav.rules", icon: SlidersHorizontal, roles: ["admin"], task: "T4.7" },
  { to: "/dashboard/models", labelKey: "nav.models", icon: Brain, roles: ["admin"], task: "T4.7" },
  { to: "/dashboard/audit", labelKey: "nav.audit", icon: ScrollText, roles: ["admin"], task: "T4.7" },
];

export const ALL_NAV = [...MAIN_NAV, ...ADMIN_NAV];
