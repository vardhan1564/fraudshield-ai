import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { LiveMonitorPage } from "@/dashboard/pages/LiveMonitorPage";
import { newPayment, TRANSACTIONS } from "@/mocks/data";
import { alertCreated } from "@/mocks/handlers";
import { server } from "@/mocks/node";
import { API_URL } from "@/shared/api/client";
import type { Page, TxnItem } from "@/shared/types/api";
import { controlAlertsSocket, renderSignedIn } from "@/test/renderSignedIn";

const TXNS = `${API_URL}/transactions`;
const FIRST = "TXN-20261020-01000"; // the newest demo payment, a HOLD
const rowOf = (txnId: string) => screen.getByText(txnId).closest("tr") as HTMLElement;
const bodyRows = () => screen.getAllByRole("row").slice(1); // minus the header

describe("Live Monitor (spec 06 §3)", () => {
  it("lists recent payments with masked payees and risk in words", async () => {
    await renderSignedIn(<LiveMonitorPage />);
    await screen.findByText(FIRST);
    const row = rowOf(FIRST);
    const vpa = TRANSACTIONS.find((t) => t.txn_id === FIRST)?.payee_vpa ?? "";
    expect(within(row).getByText(/^\w{2}\*\*\*@\w+$/)).toBeInTheDocument();
    expect(screen.queryByText(vpa)).not.toBeInTheDocument();
    expect(within(row).getByText("Held")).toBeInTheDocument();
    expect(within(row).getByText(/High|Medium/)).toBeInTheDocument();
  });

  it("shows a HOLD from /ws/alerts within 1 s, on top, with a link to its case", async () => {
    const socket = controlAlertsSocket();
    await renderSignedIn(<LiveMonitorPage />);
    await screen.findByText(FIRST);
    await socket.connected;
    expect(await screen.findByText("Live")).toBeInTheDocument();

    const held = newPayment(true);
    const sent = performance.now();
    socket.push(alertCreated(held));
    await screen.findByText(held.txn_id, {}, { timeout: 1000 });
    expect(performance.now() - sent).toBeLessThan(1000);
    const row = rowOf(held.txn_id);
    expect(bodyRows()[0]).toBe(row);
    expect(within(row).getByRole("link", { name: /Open case/ })).toHaveAttribute(
      "href",
      expect.stringMatching(/^\/dashboard\/cases\/[\w-]+$/),
    );
  });

  it("pause freezes the list and counts new payments; resume adds them", async () => {
    const socket = controlAlertsSocket();
    await renderSignedIn(<LiveMonitorPage />);
    await screen.findByText(FIRST);
    await socket.connected;
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Pause" }));

    const held = newPayment(true);
    socket.push(alertCreated(held));
    expect(await screen.findByText("1 new payment")).toBeInTheDocument();
    expect(screen.queryByText(held.txn_id)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Resume/ }));
    expect(await screen.findByText(held.txn_id)).toBeInTheDocument();
    expect(screen.queryByText("1 new payment")).not.toBeInTheDocument();
  });

  it("filters by decision and channel, with a way back when nothing matches", async () => {
    const [hold, approve] = [
      TRANSACTIONS.find((t) => t.decision === "HOLD" && t.channel === "UPI"),
      TRANSACTIONS.find((t) => t.decision === "APPROVE" && t.channel === "UPI"),
    ] as TxnItem[];
    const body: Page<TxnItem> = { items: [hold as TxnItem, approve as TxnItem], page: 1, page_size: 100, total: 2 };
    server.use(http.get(TXNS, () => HttpResponse.json(body)));
    await renderSignedIn(<LiveMonitorPage />);
    await screen.findByText(hold?.txn_id ?? "");
    const user = userEvent.setup();

    await user.click(within(screen.getByRole("radiogroup", { name: "Decision" })).getByRole("radio", { name: "Held" }));
    expect(bodyRows()).toHaveLength(1);
    expect(screen.queryByText(approve?.txn_id ?? "")).not.toBeInTheDocument();

    await user.click(within(screen.getByRole("radiogroup", { name: "Channel" })).getByRole("radio", { name: "Wallet" }));
    expect(screen.getByText("No payments match these filters")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(bodyRows()).toHaveLength(2);
  });

  it("shows the error state with Try again when payments can't load", async () => {
    let fail = true;
    server.use(
      http.get(TXNS, () =>
        fail
          ? HttpResponse.json({ error: { code: "INTERNAL_ERROR", message: "Database unavailable" } }, { status: 500 })
          : undefined, // falls through to the normal mock
      ),
    );
    await renderSignedIn(<LiveMonitorPage />);
    expect(await screen.findByText("Couldn’t load recent payments")).toBeInTheDocument();
    expect(screen.getByText("Database unavailable")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(FIRST)).toBeInTheDocument();
  });
});
