import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";

import { GraphExplorerPage } from "@/dashboard/pages/GraphExplorerPage";
import { server } from "@/mocks/node";
import type { Neighbourhood } from "@/shared/types/api";
import { renderSignedIn } from "@/test/renderSignedIn";

// jsdom has no canvas: stand in for Cytoscape with one button per node (the canvas itself is
// checked in the Playwright test e2e/graph-explorer.spec.ts).
vi.mock("@/dashboard/components/GraphView", () => ({
  GraphView: ({ graph, onNodeClick }: { graph: Neighbourhood; onNodeClick: (id: string) => void }) => (
    <div data-testid="graph-canvas" data-nodes={graph.nodes.length}>
      {graph.nodes.map((n) => (
        <button key={n.id} type="button" onClick={() => onNodeClick(n.id)}>{`node ${n.id}`}</button>
      ))}
    </div>
  ),
}));

function Where() {
  const location = useLocation();
  return <output aria-label="location">{location.pathname}</output>;
}

async function renderPage(path = "/dashboard/graph") {
  await renderSignedIn(
    <>
      <Routes>
        <Route path="/dashboard/graph/:entityId?" element={<GraphExplorerPage />} />
      </Routes>
      <Where />
    </>,
    undefined,
    path,
  );
}

function nodeCount(): number {
  return Number(screen.getByTestId("graph-canvas").getAttribute("data-nodes"));
}

function requests(match: string): string[] {
  const seen: string[] = [];
  server.events.on("request:start", ({ request }) => {
    if (request.url.includes(match)) seen.push(decodeURIComponent(request.url));
  });
  return seen;
}

describe("Graph Explorer (spec 06 §3, T5.3)", () => {
  it("lists suspected rings and opens one with the ring highlighted", async () => {
    await renderPage();
    const user = userEvent.setup();
    expect(await screen.findByText("Ring of 9 accounts")).toBeInTheDocument();
    expect(screen.getByText("Ring of 4 accounts")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open ring of 9 accounts, score 0.94" }));

    expect(screen.getByRole("status", { name: "location" })).toHaveTextContent("/dashboard/graph/A%3AU0901");
    expect(await screen.findByTestId("graph-canvas")).toBeInTheDocument();
    expect(nodeCount()).toBeGreaterThan(10);
    const inView = screen.getByRole("heading", { name: "Rings in view" }).closest("div") as HTMLElement;
    expect(within(inView).getByText("Ring of 9 accounts")).toBeInTheDocument();
    // the focus account's details: a ring member flagged by the mule model, no held payments
    expect(screen.getByRole("heading", { level: 2, name: /xxxxxx/ })).toBeInTheDocument();
    expect(screen.getByText("Ring member")).toBeInTheDocument();
    expect(screen.getByText("Mule model")).toBeInTheDocument();
    expect(screen.getByText(/Ring of 9 accounts · score 0\.94/)).toBeInTheDocument();
    expect(await screen.findByText("No held payments.")).toBeInTheDocument();
  });

  it("clicking a node loads its neighbourhood and shows its details", async () => {
    const seen = requests("/graph/entity/");
    await renderPage("/dashboard/graph/A%3AU0901");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "1 hop" }));
    await screen.findByRole("button", { name: "node A:U0400" });
    const before = nodeCount();

    await user.click(screen.getByRole("button", { name: "node A:U0400" }));
    // the victim's own phone, IP and the shops it paid join the picture
    await vi.waitFor(() => expect(nodeCount()).toBeGreaterThan(before));
    expect(seen.some((u) => u.includes("/graph/entity/A:U0400?depth=1"))).toBe(true);
    expect(screen.getByRole("heading", { level: 2, name: /xxxxxx/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Centre the graph here" })).toBeInTheDocument();
  });

  it("keeps payee ids out of the URL and links a payee's held payments to their cases", async () => {
    await renderPage();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Account or graph id"), "P:refund.helpdesk@ybl");
    await user.click(screen.getByRole("button", { name: "Open" }));

    await screen.findByTestId("graph-canvas");
    expect(screen.getByRole("status", { name: "location" })).toHaveTextContent(/^\/dashboard\/graph$/);
    const links = await screen.findAllByRole("link", { name: /^Open the case for TXN-/ });
    expect(links[0]).toHaveAttribute("href", expect.stringMatching(/^\/dashboard\/cases\/[\w-]+$/));
  });

  it("checks the id and says when there is no graph data", async () => {
    await renderPage();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Account or graph id"), "not an id!");
    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(screen.getByText("Enter an account id like U1029, or a graph id like P:name@bank")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Account or graph id"));
    await user.type(screen.getByLabelText("Account or graph id"), "U9999");
    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(await screen.findByText("No graph data for this id")).toBeInTheDocument();
  });
});
