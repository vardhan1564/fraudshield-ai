import type { Role } from "@/shared/types/api";

/** Where each role lands after login (spec 06 §1). */
export function homeFor(role: Role): string {
  return role === "customer" ? "/app" : "/dashboard";
}
