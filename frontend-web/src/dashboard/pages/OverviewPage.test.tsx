import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { OverviewPage } from "@/dashboard/pages/OverviewPage";
import { metrics } from "@/mocks/data";
import { server } from "@/mocks/node";
import { API_URL } from "@/shared/api/client";
import { login } from "@/shared/api/endpoints/auth";
import { DEMO_PASSWORD } from "@/shared/lib/demo";

const URL = `${API_URL}/metrics/overview`;

async function renderPage() {
  await login({ email: "analyst@demo.fraudshield", password: DEMO_PASSWORD });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Overview page (spec 06 §3)", () => {
  it("shows the 5 KPIs, both charts' cards and the city table from /metrics/overview", async () => {
    await renderPage();
    const kpis = await screen.findByRole("region", { name: "KPIs" });
    expect(await within(kpis).findByText("12,480")).toBeInTheDocument(); // payments checked
    expect(within(kpis).getByText("214")).toBeInTheDocument(); // fraud caught
    expect(within(kpis).getByText("₹38.45L")).toHaveAttribute("title", "₹38,45,200");
    expect(within(kpis).getByText("0.64%")).toBeInTheDocument();
    expect(within(kpis).getByText("34 ms")).toBeInTheDocument();
    expect(within(kpis).getByText("Within the 100 ms budget")).toBeInTheDocument();
    expect(screen.getByText("Activity by hour (IST)")).toBeInTheDocument();
    expect(screen.getByText("Fraud by type")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Mumbai" })).toBeInTheDocument();
  });

  it("can show a chart's numbers as a table (no value is hover-only)", async () => {
    await renderPage();
    const card = (await screen.findByText("Fraud by type")).closest("div.rounded-xl") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Table" }));
    expect(within(card).getByRole("cell", { name: "Digital arrest" })).toBeInTheDocument();
    expect(within(card).getByRole("cell", { name: "61" })).toBeInTheDocument();
  });

  it("flags a p95 over the 100 ms budget in words", async () => {
    server.use(http.get(URL, () => HttpResponse.json({ ...metrics(), p95_latency_ms: 134 })));
    await renderPage();
    expect(await screen.findByText("Over the 100 ms budget")).toBeInTheDocument();
  });

  it("shows the empty state when nothing was scored in the period", async () => {
    server.use(
      http.get(URL, () =>
        HttpResponse.json({ ...metrics(), txn_count: 0, fraud_caught: 0, amount_saved_inr: 0,
          false_positive_rate: null, p95_latency_ms: null, by_type: [], by_city: [] }),
      ),
    );
    await renderPage();
    expect(await screen.findByText("No payments in this period yet")).toBeInTheDocument();
    expect(screen.getByText("No labelled cases yet")).toBeInTheDocument();
  });

  it("shows the error state with Try again, which recovers", async () => {
    let fail = true;
    server.use(
      http.get(URL, () =>
        fail
          ? HttpResponse.json({ error: { code: "INTERNAL_ERROR", message: "Database unavailable" } }, { status: 500 })
          : HttpResponse.json(metrics()),
      ),
    );
    await renderPage();
    expect(await screen.findByText("Couldn't load the overview")).toBeInTheDocument();
    expect(screen.getByText("Database unavailable")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("12,480")).toBeInTheDocument();
  });
});
