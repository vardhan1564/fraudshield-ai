import axios, { AxiosError, type AxiosRequestConfig, type InternalAxiosRequestConfig } from "axios";

import { tokens } from "@/shared/api/tokens";
import type { ApiErrorBody, TokenResponse } from "@/shared/types/api";

export const API_URL: string = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";

/** A failed call, in the API's own words: `{"error": {"code", "message"}}` (spec 04). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function isErrorBody(data: unknown): data is ApiErrorBody {
  return typeof data === "object" && data !== null && "error" in data;
}

export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  if (axios.isAxiosError(err)) {
    const status = err.response?.status ?? 0;
    const data: unknown = err.response?.data;
    if (isErrorBody(data)) {
      return new ApiError(status, data.error.code, data.error.message, data.error.details);
    }
    if (status === 0) return new ApiError(0, "NETWORK_ERROR", "Cannot reach the server");
    return new ApiError(status, "HTTP_ERROR", err.message);
  }
  return new ApiError(0, "UNKNOWN", err instanceof Error ? err.message : "Something went wrong");
}

/** No interceptors: used for /auth/refresh itself. */
const raw = axios.create({ baseURL: API_URL, timeout: 15_000 });

let refreshing: Promise<string> | null = null;

/**
 * Get a new access token. Concurrent callers share one request: refresh tokens rotate (each
 * works once, spec 04 §2.9), so two parallel refreshes would log the user out.
 */
export function refreshAccessToken(): Promise<string> {
  refreshing ??= (async () => {
    const refreshToken = tokens.refresh;
    if (!refreshToken) throw new ApiError(401, "UNAUTHORIZED", "Please log in");
    try {
      const { data } = await raw.post<TokenResponse>("/auth/refresh", {
        refresh_token: refreshToken,
      });
      tokens.set(data);
      return data.access_token;
    } catch (err) {
      const apiErr = toApiError(err);
      if (apiErr.status === 401) tokens.clear(); // expired or reused: back to login
      throw apiErr;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

export const api = axios.create({ baseURL: API_URL, timeout: 15_000 });

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (tokens.access) config.headers.set("Authorization", `Bearer ${tokens.access}`);
  return config;
});

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean };

/** Calls whose 401 means "wrong credentials", not "token expired": never refresh for them. */
const NO_REFRESH = new Set(["/auth/login", "/auth/refresh", "/auth/register"]);

api.interceptors.response.use(undefined, async (error: unknown) => {
  if (!(error instanceof AxiosError) || !error.config) throw toApiError(error);
  const config = error.config as RetriableConfig;
  const credentialsCall = NO_REFRESH.has(config.url ?? "");
  if (error.response?.status === 401 && !config._retried && !credentialsCall && tokens.refresh) {
    config._retried = true;
    const access = await refreshAccessToken();
    config.headers.set("Authorization", `Bearer ${access}`);
    return api.request(config);
  }
  throw toApiError(error);
});

/** Typed helpers: return the body, throw ApiError. */
export async function get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  return (await api.get<T>(url, config)).data;
}
export async function post<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return (await api.post<T>(url, body, config)).data;
}
export async function patch<T>(url: string, body?: unknown): Promise<T> {
  return (await api.patch<T>(url, body)).data;
}
export async function put<T>(url: string, body?: unknown): Promise<T> {
  return (await api.put<T>(url, body)).data;
}
export async function del(url: string): Promise<void> {
  await api.delete(url);
}
