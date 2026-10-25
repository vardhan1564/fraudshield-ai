import { createBrowserRouter, type RouteObject } from "react-router-dom";

import { PhoneFrame } from "@/customer/layout/PhoneFrame";
import { HomePage } from "@/customer/pages/HomePage";
import { PayPage } from "@/customer/pages/PayPage";
import { PaymentDonePage } from "@/customer/pages/PaymentDonePage";
import { ReportPage } from "@/customer/pages/ReportPage";
import { RiskWarningPage } from "@/customer/pages/RiskWarningPage";
import { ScamCoachPage } from "@/customer/pages/ScamCoachPage";
import { SettingsPage } from "@/customer/pages/SettingsPage";
import { DashboardLayout } from "@/dashboard/layout/DashboardLayout";
import { ALL_NAV } from "@/dashboard/layout/nav";
import { AlertQueuePage } from "@/dashboard/pages/AlertQueuePage";
import { AuditPage } from "@/dashboard/pages/AuditPage";
import { CaseDetailPage } from "@/dashboard/pages/CaseDetailPage";
import { ComingSoonPage } from "@/dashboard/pages/ComingSoonPage";
import { LiveMonitorPage } from "@/dashboard/pages/LiveMonitorPage";
import { ModelsPage } from "@/dashboard/pages/ModelsPage";
import { OverviewPage } from "@/dashboard/pages/OverviewPage";
import { PayeesPage } from "@/dashboard/pages/PayeesPage";
import { RulesPage } from "@/dashboard/pages/RulesPage";
import { CustomerPlaceholder, RootRedirect } from "@/routeElements";
import { RequireAuth } from "@/shared/components/RequireAuth";
import { LoginPage } from "@/shared/pages/LoginPage";
import { NotFoundPage } from "@/shared/pages/NotFoundPage";

const BUILT = new Set([
  "/dashboard", "/dashboard/live", "/dashboard/alerts", "/dashboard/graph", "/dashboard/payees",
  "/dashboard/rules", "/dashboard/models", "/dashboard/audit",
]);

export const routes: RouteObject[] = [
  { path: "/", element: <RootRedirect /> },
  { path: "/login", element: <LoginPage /> },
  {
    // Analyst and admin dashboard (spec 06).
    element: <RequireAuth roles={["analyst", "admin"]} />,
    children: [
      {
        path: "/dashboard",
        element: <DashboardLayout />,
        children: [
          { index: true, element: <OverviewPage /> },
          { path: "live", element: <LiveMonitorPage /> },
          { path: "alerts", element: <AlertQueuePage /> },
          {
            // loaded on first visit: Cytoscape is ~400 kB the rest of the app never needs
            path: "graph/:entityId?",
            lazy: () => import("@/dashboard/pages/GraphExplorerPage").then((m) => ({ Component: m.GraphExplorerPage })),
          },
          { path: "payees", element: <PayeesPage /> },
          {
            // admin pages: blocked by URL too, not only hidden from the menu (T4.7)
            element: <RequireAuth roles={["admin"]} />,
            children: [
              { path: "rules", element: <RulesPage /> },
              { path: "models", element: <ModelsPage /> },
              { path: "audit", element: <AuditPage /> },
            ],
          },
          ...ALL_NAV.filter((item) => !BUILT.has(item.to)).map((item) => ({
            path: item.to.replace("/dashboard/", ""),
            element: <ComingSoonPage task={item.task} />,
          })),
          { path: "cases/:caseId", element: <CaseDetailPage /> },
        ],
      },
    ],
  },
  {
    // Customer app (spec 07), phone-sized.
    element: <RequireAuth roles={["customer"]} />,
    children: [
      {
        path: "/app",
        element: <PhoneFrame />,
        children: [
          { index: true, element: <HomePage /> },
          { path: "pay", element: <PayPage /> },
          { path: "pay/:txnId/warning", element: <RiskWarningPage /> },
          { path: "pay/:txnId/coach", element: <ScamCoachPage /> },
          { path: "pay/:txnId/done", element: <PaymentDonePage /> },
          { path: "report", element: <ReportPage /> },
          { path: "safety", element: <CustomerPlaceholder index={3} /> },
          { path: "settings", element: <SettingsPage /> },
        ],
      },
    ],
  },
  { path: "*", element: <NotFoundPage /> },
];

export function createRouter() {
  return createBrowserRouter(routes);
}
