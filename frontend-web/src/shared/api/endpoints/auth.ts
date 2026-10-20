import { get, post } from "@/shared/api/client";
import { tokens } from "@/shared/api/tokens";
import type { LoginRequest, Me, RegisterRequest, TokenResponse } from "@/shared/types/api";

export async function login(body: LoginRequest): Promise<Me> {
  tokens.set(await post<TokenResponse>("/auth/login", body));
  return me();
}

export async function register(body: RegisterRequest): Promise<Me> {
  tokens.set(await post<TokenResponse>("/auth/register", body));
  return me();
}

export function me(): Promise<Me> {
  return get<Me>("/auth/me");
}
