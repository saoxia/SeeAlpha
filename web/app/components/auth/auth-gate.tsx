"use client";

import { useEffect, useState } from "react";
import { TradingSystemApp } from "../trading-system-app";
import { LoginPage } from "./login-page";
import { SeeAlphaLogo } from "../brand/seealpha-logo";
import { sessionFromAuth, type AuthSession } from "../../features/identity/model";

export type { AuthUser } from "../../features/identity/model";

type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; session: AuthSession };

export function AuthGate() {
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/auth/me", {
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          setAuth({ status: "anonymous" });
          return;
        }
        const session = sessionFromAuth(await response.json());
        setAuth(session ? { status: "authenticated", session } : { status: "anonymous" });
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setAuth({ status: "anonymous" });
      });
    return () => controller.abort();
  }, []);

  async function logout() {
    await fetch("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    });
    setAuth({ status: "anonymous" });
  }

  if (auth.status === "loading") {
    return (
      <main className="auth-loading" aria-label="正在检查登录状态">
        <SeeAlphaLogo className="loading-brand" />
        <p>正在连接交易系统…</p>
      </main>
    );
  }

  if (auth.status === "anonymous") {
    return (
      <LoginPage
        onAuthenticated={(session) => setAuth({ status: "authenticated", session })}
      />
    );
  }

  return (
    <TradingSystemApp
      session={auth.session}
      onSession={(session) => setAuth({ status: "authenticated", session })}
      onLogout={logout}
    />
  );
}
