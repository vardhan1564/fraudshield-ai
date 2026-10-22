import { get, patch, post } from "@/shared/api/client";
import { tokens } from "@/shared/api/tokens";
import type { Language, LoginRequest, Me, RegisterRequest, TokenResponse } from "@/shared/types/api";

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

/** Save the language to the profile (T4.5): the next payment's warning and reasons use it. */
export function updateMe(body: { language: Language }): Promise<Me> {
  return patch<Me>("/auth/me", body);
}
