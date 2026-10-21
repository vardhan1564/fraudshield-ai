import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { AlertQueuePage } from "@/dashboard/pages/AlertQueuePage";
import { CASES, newPayment, USERS } from "@/mocks/data";
import { alertCreated } from "@/mocks/handlers";
import { server } from "@/mocks/node";
import { API_URL } from "@/shared/api/client";
import type { AlertItem, Page, Severity } from "@/shared/types/api";
import { controlAlertsSocket, renderSignedIn } from "@/test/renderSignedIn";

const ANALYST_ID = USERS.find((u) => u.role === "analyst")?.id ?? "";
const bodyRows = () => screen.getAllByRole("row").slice(1);
const txnIds = () => bodyRows().map((r) => within(r).getByText(/^TXN-/).textContent);

function alert(id: string, severity: Severity, minutesAgo: number, amount: number, assigned: string | null = null): AlertItem {
  return {
    alert_id: `a-${id}`, case_id: `c-${id}`, txn_id: `TXN-${id}`, severity, status: assigned ? "assigned" : "open",
    assigned_to: assigned, risk_score: 0.8, decision: "HOLD", amount, channel: "UPI",
    top_reasons: [{ feature: "payee_report_count", value: 7, impact: 1.2, text: "Payee reported by 7 users" }],
    created_at: new Date(Date.now() - minutesAgo * 60_000).toISOString(),
  };
}

function serveAlerts(items: AlertItem[]) {
  const body: Page<AlertItem> = { items, page: 1, page_size: 200, total: items.length };
  server.use(http.get(`${API_URL}/alerts`, () => HttpResponse.json(body)));
}

describe("Alert Queue (spec 06 §3)", () => {
  it("shows each alert's SLA in words: time left, due soon, overdue", async () => {
    serveAlerts([alert("A", "high", 20, 90_000), alert("B", "medium", 50, 40_000), alert("C", "low", 10, 5_000)]);
    await renderSignedIn(<AlertQueuePage />);
    expect(await screen.findByText("3 open alerts")).toBeInTheDocument();
    expect(txnIds()).toEqual(["TXN-A", "TXN-B", "TXN-C"]); // most severe first
    expect(within(bodyRows()[0] as HTMLElement).getByText(/^Overdue by 5m/)).toBeInTheDocument();
    expect(within(bodyRows()[1] as HTMLElement).getByText(/^Due soon · (9|10)m/)).toBeInTheDocument();
    expect(within(bodyRows()[2] as HTMLElement).getByText(/^3h (49|50)m left/)).toBeInTheDocument();
  });

  it("sorts by a column on click, and flips on a second click", async () => {
    serveAlerts([alert("A", "high", 20, 90_000), alert("B", "medium", 50, 40_000), alert("C", "low", 10, 95_000)]);
    await renderSignedIn(<AlertQueuePage />);
    await screen.findByText("TXN-A");
    const user = userEvent.setup();
    const amount = screen.getByRole("button", { name: "Amount" });

    await user.click(amount);
    expect(txnIds()).toEqual(["TXN-C", "TXN-A", "TXN-B"]);
    expect(amount.closest("th")).toHaveAttribute("aria-sort", "descending");
    await user.click(amount);
    expect(txnIds()).toEqual(["TXN-B", "TXN-A", "TXN-C"]);

    await user.click(screen.getByRole("button", { name: "Waiting" }));
    expect(txnIds()).toEqual(["TXN-B", "TXN-A", "TXN-C"]); // longest waiting first
  });

  it("assign to me updates the row at once and saves it", async () => {
    await renderSignedIn(<AlertQueuePage />);
    await screen.findByText(/open alerts/);
    const target = CASES.find((c) => c.assigned_to === null && c.status === "open");
    const row = screen.getByText(target?.transaction.txn_id ?? "").closest("tr") as HTMLElement;
    const user = userEvent.setup();

    await user.click(within(row).getByRole("button", { name: "Assign to me" }));
    expect(await within(row).findByText("You")).toBeInTheDocument();
    expect(target?.assigned_to).toBe(ANALYST_ID); // the mock API got the PATCH

    await user.click(within(screen.getByRole("radiogroup", { name: "Show" })).getByRole("radio", { name: "Assigned to me" }));
    expect(screen.getByText(target?.transaction.txn_id ?? "")).toBeInTheDocument();
  });

  it("puts the row back and says why when assigning fails", async () => {
    serveAlerts([alert("A", "high", 2, 90_000)]);
    server.use(
      http.patch(`${API_URL}/cases/:id`, () =>
        HttpResponse.json({ error: { code: "NOT_FOUND", message: "Case not found" } }, { status: 404 }),
      ),
    );
    await renderSignedIn(<AlertQueuePage />);
    await screen.findByText("TXN-A");
    await userEvent.click(screen.getByRole("button", { name: "Assign to me" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t assign the alert: Case not found");
    expect(screen.getByRole("button", { name: "Assign to me" })).toBeEnabled();
    expect(screen.queryByText("You")).not.toBeInTheDocument();
  });

  it("refreshes by itself when /ws/alerts announces a new alert", async () => {
    const socket = controlAlertsSocket();
    await renderSignedIn(<AlertQueuePage />);
    await screen.findByText(/open alerts/);
    await socket.connected;
    const held = newPayment(true);
    socket.push(alertCreated(held));
    expect(await screen.findByText(held.txn_id, {}, { timeout: 1000 })).toBeInTheDocument();
  });

  it("shows the empty state when the queue is clear", async () => {
    serveAlerts([]);
    await renderSignedIn(<AlertQueuePage />);
    expect(await screen.findByText("No open alerts")).toBeInTheDocument();
  });

  it("shows the error state with Try again", async () => {
    server.use(
      http.get(`${API_URL}/alerts`, () =>
        HttpResponse.json({ error: { code: "INTERNAL_ERROR", message: "Database unavailable" } }, { status: 500 }),
      ),
    );
    await renderSignedIn(<AlertQueuePage />);
    expect(await screen.findByText("Couldn’t load the alert queue")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
