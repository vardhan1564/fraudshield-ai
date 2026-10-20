import { setupServer } from "msw/node";

import { handlers } from "@/mocks/handlers";

/** The same mocks for Vitest (jsdom). */
export const server = setupServer(...handlers);
