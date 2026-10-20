import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { routes } from "@/routes";
import { AuthProvider } from "@/shared/components/AuthProvider";
import { setLanguage } from "@/shared/i18n";
import { DEMO_PASSWORD } from "@/shared/lib/demo";

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  );
  return router;
}

async function signIn(email: string, password = DEMO_PASSWORD) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Email"), email);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("login and role-based redirect (spec 06 §1)", () => {
  it("sends analysts to /dashboard", async () => {
    const router = renderAt("/login");
    await signIn("analyst@demo.fraudshield");
    expect(await screen.findByRole("navigation", { name: "Dashboard" }, { timeout: 3000 })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/dashboard");
  });

  it("sends customers to /app", async () => {
    const router = renderAt("/login");
    await signIn("customer@demo.fraudshield");
    expect(await screen.findByText("Hello, Ravi")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app");
  });

  it("shows the server's error in plain words on a wrong password", async () => {
    renderAt("/login");
    await signIn("analyst@demo.fraudshield", "not-the-password");
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong email or password.");
  });

  it("protects pages: logged out -> /login, wrong role -> own home", async () => {
    const router = renderAt("/dashboard/alerts");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    await signIn("customer@demo.fraudshield"); // a customer asked for the dashboard ...
    expect(await screen.findByText("Hello, Ravi")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app"); // ... and lands in the app instead
  });

  it("translates the form (hi)", async () => {
    setLanguage("hi");
    renderAt("/login");
    expect(await screen.findByRole("heading", { name: "साइन इन करें" })).toBeInTheDocument();
  });
});
