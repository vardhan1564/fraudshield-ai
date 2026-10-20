import { createBrowserRouter, type RouteObject } from "react-router-dom";

import { PhoneFrame } from "@/customer/layout/PhoneFrame";
import { HomePage } from "@/customer/pages/HomePage";
import { SettingsPage } from "@/customer/pages/SettingsPage";
import { DashboardLayout } from "@/dashboard/layout/DashboardLayout";
import { ALL_NAV } from "@/dashboard/layout/nav";
import { ComingSoonPage } from "@/dashboard/pages/ComingSoonPage";
import { OverviewPage } from "@/dashboard/pages/OverviewPage";
import { CustomerPlaceholder, RootRedirect } from "@/routeElements";
import { RequireAuth } from "@/shared/components/RequireAuth";
import { LoginPage } from "@/shared/pages/LoginPage";
import { NotFoundPage } from "@/shared/pages/NotFoundPage";

export const routes: RouteObject[] = [
  { path: "/", element: <RootRedirect /> },
  { path: "/login", element: <LoginPage /> },
  {
    // Analyst and admin dashboard (spec 06). Placeholders until T4.2-T4.7 / T5.3.
    element: <RequireAuth roles={["analyst", "admin"]} />,
    children: [
      {
        path: "/dashboard",
        element: <DashboardLayout />,
        children: [
          ...ALL_NAV.map((item) =>
            item.to === "/dashboard"
              ? { index: true, element: <OverviewPage /> }
              : { path: item.to.replace("/dashboard/", ""), element: <ComingSoonPage task={item.task} /> },
          ),
          { path: "cases/:caseId", element: <ComingSoonPage task="T4.4" /> },
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
          { path: "pay", element: <CustomerPlaceholder index={1} /> },
          { path: "report", element: <CustomerPlaceholder index={2} /> },
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
