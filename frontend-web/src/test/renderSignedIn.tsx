import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";

import { alertsLink } from "@/mocks/handlers";
import { server } from "@/mocks/node";
import { login } from "@/shared/api/endpoints/auth";
import { AuthProvider } from "@/shared/components/AuthProvider";
import { DEMO_PASSWORD } from "@/shared/lib/demo";

/** Render a page as a signed-in user (the stored refresh token restores the session, as on reload). */
export async function renderSignedIn(ui: ReactElement, email = "analyst@demo.fraudshield") {
  await login({ email, password: DEMO_PASSWORD });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <MemoryRouter>{ui}</MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
  return client;
}

/** Push messages on `/ws/alerts` from a test: `await connected` resolves once the page subscribed. */
export function controlAlertsSocket() {
  let push: (message: object) => void = () => undefined;
  const connected = new Promise<void>((resolve) => {
    server.use(
      alertsLink.addEventListener("connection", ({ client }) => {
        push = (message) => client.send(JSON.stringify(message));
        resolve();
      }),
    );
  });
  return { connected, push: (message: object) => push(message) };
}
