import "@testing-library/jest-dom/vitest";
import "@/shared/i18n";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { server } from "@/mocks/node";
import { tokens } from "@/shared/api/tokens";
import { setLanguage } from "@/shared/i18n";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  tokens.clear();
  window.localStorage.clear();
  setLanguage("en");
});
afterAll(() => server.close());
