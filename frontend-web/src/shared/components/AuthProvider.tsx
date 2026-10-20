import { useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";

import { refreshAccessToken } from "@/shared/api/client";
import * as authApi from "@/shared/api/endpoints/auth";
import { tokens } from "@/shared/api/tokens";
import { setLanguage } from "@/shared/i18n";
import { AuthContext, type AuthState } from "@/shared/hooks/authContext";
import type { LoginRequest, Me } from "@/shared/types/api";





export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<AuthState>({ status: "loading", user: null });

  const signedIn = useCallback((user: Me) => {
    setLanguage(user.language); // the profile language wins
    setState({ status: "authenticated", user });
  }, []);

  // On start: a stored refresh token restores the session.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!tokens.refresh) {
        setState({ status: "anonymous", user: null });
        return;
      }
      try {
        await refreshAccessToken();
        const user = await authApi.me();
        if (!cancelled) signedIn(user);
      } catch {
        if (!cancelled) setState({ status: "anonymous", user: null });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [signedIn]);

  // A failed refresh anywhere (expired/reused token) logs out.
  useEffect(
    () =>
      tokens.onCleared(() => {
        queryClient.clear();
        setState({ status: "anonymous", user: null });
      }),
    [queryClient],
  );

  const login = useCallback(
    async (body: LoginRequest) => {
      const user = await authApi.login(body);
      signedIn(user);
      return user;
    },
    [signedIn],
  );

  const logout = useCallback(() => {
    tokens.clear();
    queryClient.clear();
    setState({ status: "anonymous", user: null });
  }, [queryClient]);

  const value = useMemo(() => ({ state, login, logout }), [state, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
