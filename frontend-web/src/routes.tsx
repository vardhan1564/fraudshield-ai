import { createBrowserRouter, type RouteObject } from "react-router-dom";

import { PhoneFrame } from "@/customer/layout/PhoneFrame";
import { HomePage } from "@/customer/pages/HomePage";
import { PayPage } from "@/customer/pages/PayPage";
import { PaymentDonePage } from "@/customer/pages/PaymentDonePage";
import { ReportPage } from "@/customer/pages/ReportPage";
import { RiskWarningPage } from "@/customer/pages/RiskWarningPage";
import { SettingsPage } from "@/customer/pages/SettingsPage";
import { DashboardLayout } from "@/dashboard/layout/DashboardLayout";
import { ALL_NAV } from "@/dashboard/layout/nav";
import { AlertQueuePage } from "@/dashboard/pages/AlertQueuePage";
import { CaseDetailPage } from "@/dashboard/pages/CaseDetailPage";
import { ComingSoonPage } from "@/dashboard/pages/ComingSoonPage";
import { LiveMonitorPage } from "@/dashboard/pages/LiveMonitorPage";
import { OverviewPage } from "@/dashboard/pages/OverviewPage";
import { CustomerPlaceholder, RootRedirect } from "@/routeElements";
import { RequireAuth } from "@/shared/components/RequireAuth";
import { LoginPage } from "@/shared/pages/LoginPage";
import { NotFoundPage } from "@/shared/pages/NotFoundPage";

const BUILT = new Set(["/dashboard", "/dashboard/live", "/dashboard/alerts"]);

export const routes: RouteObject[] = [
  { path: "/", element: <RootRedirect /> },
  { path: "/login", element: <LoginPage /> },
  {
    // Analyst and admin dashboard (spec 06). Placeholders until T4.7 / T5.3.
    element: <RequireAuth roles={["analyst", "admin"]} />,
    children: [
      {
        path: "/dashboard",
        element: <DashboardLayout />,
        children: [
          { index: true, element: <OverviewPage /> },
          { path: "live", element: <LiveMonitorPage /> },
          { path: "alerts", element: <AlertQueuePage /> },
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
