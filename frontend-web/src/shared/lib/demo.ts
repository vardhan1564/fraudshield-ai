import type { Role } from "@/shared/types/api";

/** `npm run dev:mock` runs the UI on MSW mocks (no backend needed). */
export const IS_MOCK: boolean = import.meta.env.MODE === "mock" || import.meta.env.MODE === "test";

export const DEMO_PASSWORD = "demo-pass-123";

/** Accounts the mock backend knows. Not real users. */
export const DEMO_ACCOUNTS: { email: string; name: string; role: Role }[] = [
  { email: "analyst@demo.fraudshield", name: "Asha Rao", role: "analyst" },
  { email: "admin@demo.fraudshield", name: "Meera Iyer", role: "admin" },
  { email: "customer@demo.fraudshield", name: "Ravi Kumar", role: "customer" },
];
