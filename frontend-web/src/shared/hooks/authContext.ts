import { createContext } from "react";

import type { LoginRequest, Me } from "@/shared/types/api";

export type AuthState =
  | { status: "loading"; user: null }
  | { status: "anonymous"; user: null }
  | { status: "authenticated"; user: Me };

export interface AuthContextValue {
  state: AuthState;
  login: (body: LoginRequest) => Promise<Me>;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
