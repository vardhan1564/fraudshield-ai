import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { expireAccessTokens } from "@/mocks/handlers";
import { server } from "@/mocks/node";
import { API_URL, ApiError, get } from "@/shared/api/client";
import { login } from "@/shared/api/endpoints/auth";
import { tokens } from "@/shared/api/tokens";
import { DEMO_PASSWORD } from "@/shared/lib/demo";
import type { Me } from "@/shared/types/api";

const ANALYST = { email: "analyst@demo.fraudshield", password: DEMO_PASSWORD };

describe("api client", () => {
  it("logs in, stores the tokens and sends the bearer token", async () => {
    const me = await login(ANALYST);
    expect(me.role).toBe("analyst");
    expect(tokens.access).toMatch(/^mock-access-/);
    expect(tokens.refresh).toMatch(/^mock-refresh-/);
    expect((await get<Me>("/auth/me")).email).toBe(ANALYST.email);
  });

  it("refreshes an expired access token once for many parallel calls, then retries them", async () => {
    await login(ANALYST);
    const firstRefresh = tokens.refresh;
    expireAccessTokens(); // every access token is now rejected with 401
    let refreshCalls = 0;
    server.events.on("request:start", ({ request }) => {
      if (request.url.endsWith("/auth/refresh")) refreshCalls += 1;
    });

    const results = await Promise.all([get<Me>("/auth/me"), get<Me>("/auth/me"), get<Me>("/auth/me")]);

    expect(results.map((r) => r.role)).toEqual(["analyst", "analyst", "analyst"]);
    expect(refreshCalls).toBe(1); // refresh tokens rotate: a second refresh would log us out
    expect(tokens.refresh).not.toBe(firstRefresh);
    server.events.removeAllListeners();
  });

  it("clears the session when the refresh token is rejected", async () => {
    await login(ANALYST);
    expireAccessTokens();
    server.use(
      http.post(`${API_URL}/auth/refresh`, () =>
        HttpResponse.json({ error: { code: "UNAUTHORIZED", message: "Invalid token" } }, { status: 401 }),
      ),
    );
    let cleared = false;
    const stop = tokens.onCleared(() => {
      cleared = true;
    });

    await expect(get<Me>("/auth/me")).rejects.toMatchObject({ status: 401, code: "UNAUTHORIZED" });
    expect(cleared).toBe(true);
    expect(tokens.refresh).toBeNull();
    stop();
  });

  it("turns error bodies into ApiError with the server's code and message", async () => {
    const err = await login({ ...ANALYST, password: "wrong" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 401, code: "UNAUTHORIZED", message: "Wrong email or password" });
  });

  it("reports a network failure as NETWORK_ERROR", async () => {
    server.use(http.get(`${API_URL}/metrics/overview`, () => HttpResponse.error()));
    await expect(get("/metrics/overview")).rejects.toMatchObject({ code: "NETWORK_ERROR" });
  });
});
