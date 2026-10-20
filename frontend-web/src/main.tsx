import "@/index.css";
import "@/shared/i18n";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "@/App";
import { IS_MOCK } from "@/shared/lib/demo";

async function start(): Promise<void> {
  if (IS_MOCK) {
    // `npm run dev:mock`: answer the API from MSW (no backend needed).
    const { worker } = await import("@/mocks/browser");
    await worker.start({ onUnhandledRequest: "bypass", quiet: true });
  }
  const root = document.getElementById("root");
  if (!root) throw new Error("#root missing in index.html");
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void start();
