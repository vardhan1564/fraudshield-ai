import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { QUEUE_KEY } from "@/dashboard/hooks/useAlertStream";
import { CaseDetailPage } from "@/dashboard/pages/CaseDetailPage";
import { alertOf, CASES } from "@/mocks/data";
import { server } from "@/mocks/node";
import { API_URL } from "@/shared/api/client";
import type { AlertItem, CaseDetail, CaseUpdate, Page } from "@/shared/types/api";
import { controlAlertsSocket, renderSignedIn } from "@/test/renderSignedIn";

/** Each test takes its own open case: the mock API keeps PATCHed state for the whole file. */
let next = 0;
function openCase(): CaseDetail {
  const found = CASES.filter((c) => c.status !== "closed")[next];
  next += 1;
  if (!found) throw new Error("not enough open demo cases");
  return found;
}

async function renderCase(c: CaseDetail) {
  const client = await renderSignedIn(
    <Routes>
      <Route path="/dashboard/cases/:caseId" element={<CaseDetailPage />} />
      <Route path="/dashboard/alerts" element={<p>queue page</p>} />
    </Routes>,
    undefined,
    `/dashboard/cases/${c.case_id}`,
  );
  await screen.findByText(c.transaction.txn_id);
  return client;
}

function seedQueue(client: Awaited<ReturnType<typeof renderCase>>) {
  const items = CASES.filter((c) => c.status !== "closed").map(alertOf);
  client.setQueryData<Page<AlertItem>>(QUEUE_KEY, { items, page: 1, page_size: 200, total: items.length });
  return () => client.getQueryData<Page<AlertItem>>(QUEUE_KEY)?.items.map((a) => a.alert_id) ?? [];
}

describe("Case Detail (spec 06 §3)", () => {
  it("shows the payment, explanation, model drivers, device and the customer's history", async () => {
    const c = openCase();
    await renderCase(c);
    expect(screen.getByRole("heading", { name: "Payment" })).toBeInTheDocument();
    expect(screen.getByText(c.transaction.customer.name)).toBeInTheDocument();
    expect(screen.getByText(c.explanation ?? "")).toBeInTheDocument();
    expect(screen.getByText(c.top_reasons[0]?.text ?? "")).toBeInTheDocument();
    expect(await screen.findByText("What drove the score")).toBeInTheDocument();
    expect(await screen.findByText("D-88a1")).toBeInTheDocument(); // device
    const history = screen.getByRole("heading", { name: "Customer's recent payments" }).closest("div")?.parentElement as HTMLElement;
    expect(await within(history).findByText(/^Latest 10 of/)).toBeInTheDocument();
  });

  it("confirm fraud: closed at once, toast, PATCH sent, alert leaves the queue", async () => {
    const c = openCase();
    let sent: CaseUpdate | null = null;
    server.use(
      http.patch(`${API_URL}/cases/:id`, async ({ request }) => {
        sent = (await request.json()) as CaseUpdate;
        await new Promise((r) => setTimeout(r, 50)); // the UI must not wait for this
        return HttpResponse.json({ ...c, status: "closed", label: "fraud", resolved_at: new Date().toISOString() });
      }),
    );
    const client = await renderCase(c);
    const queue = seedQueue(client);
    expect(queue()).toContain(c.alert_id);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/^Note/), "Customer confirmed by phone");
    await user.click(screen.getByRole("button", { name: "Confirm fraud" }));

    expect(screen.getAllByText("Confirmed as fraud")[0]).toBeInTheDocument(); // optimistic
    expect(queue()).not.toContain(c.alert_id);
    expect(screen.queryByRole("button", { name: "Confirm fraud" })).not.toBeInTheDocument();
    expect(await screen.findByText(`${c.transaction.txn_id} · the alert queue is updated`)).toBeInTheDocument();
    expect(sent).toEqual({ label: "fraud", status: "closed", notes: "Customer confirmed by phone" });
  });

  it("escalate needs a reason, then raises severity and puts the alert back in the queue", async () => {
    const c = openCase();
    const client = await renderCase(c);
    const queue = seedQueue(client);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Escalate" }));
    expect(screen.getByText(/Write why you are escalating/)).toBeInTheDocument();

    await user.type(screen.getByLabelText(/^Note/), "Ring of mule accounts, needs a senior");
    await user.click(screen.getByRole("button", { name: "Escalate" }));
    expect(await screen.findByText("Escalated")).toBeInTheDocument();
    expect(c).toMatchObject({ severity: "high", status: "open", assigned_to: null }); // mock API state
    expect(queue()).toContain(c.alert_id);
    expect(screen.getByRole("button", { name: "Confirm fraud" })).toBeInTheDocument(); // still open
  });

  it("puts everything back and says why when saving fails", async () => {
    const c = openCase();
    server.use(
      http.patch(`${API_URL}/cases/:id`, () =>
        HttpResponse.json({ error: { code: "INTERNAL_ERROR", message: "Something went wrong" } }, { status: 500 }),
      ),
    );
    const client = await renderCase(c);
    const queue = seedQueue(client);
    await userEvent.setup().click(screen.getByRole("button", { name: "Mark legit" }));

    expect(await screen.findByText("Not saved")).toBeInTheDocument();
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark legit" })).toBeEnabled();
    expect(queue()).toContain(c.alert_id);
  });

  it("replaces the explanation when the worker's sentence arrives (explanation.ready)", async () => {
    const c = openCase();
    const socket = controlAlertsSocket();
    await renderCase(c);
    await socket.connected;

    socket.push({ type: "explanation.ready", data: { txn_id: "TXN-SOMEONE-ELSE", explanation: "Not for this case" } });
    socket.push({ type: "explanation.ready", data: { txn_id: c.transaction.txn_id, explanation: "Caller posed as CBI." } });

    expect(await screen.findByText("Caller posed as CBI.")).toBeInTheDocument();
    expect(screen.getByText("Updated just now")).toBeInTheDocument();
    expect(screen.queryByText("Not for this case")).not.toBeInTheDocument();
  });

  it("says when the case does not exist", async () => {
    await renderSignedIn(
      <Routes>
        <Route path="/dashboard/cases/:caseId" element={<CaseDetailPage />} />
      </Routes>,
      undefined,
      "/dashboard/cases/00000000-0000-4000-8000-000000000000",
    );
    expect(await screen.findByText("Case not found")).toBeInTheDocument();
  });
});
