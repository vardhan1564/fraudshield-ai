import { Flag, Home, type LucideIcon, Send, Settings, ShieldCheck } from "lucide-react";

type Key = "nav.home" | "nav.pay" | "nav.report" | "nav.safety" | "nav.settings";

export const CUSTOMER_NAV: { to: string; labelKey: Key; icon: LucideIcon; task: string }[] = [
  { to: "/app", labelKey: "nav.home", icon: Home, task: "T4.5" },
  { to: "/app/pay", labelKey: "nav.pay", icon: Send, task: "T4.5" },
  { to: "/app/report", labelKey: "nav.report", icon: Flag, task: "T4.6" },
  { to: "/app/safety", labelKey: "nav.safety", icon: ShieldCheck, task: "T5.6" },
  { to: "/app/settings", labelKey: "nav.settings", icon: Settings, task: "T4.5" },
];
