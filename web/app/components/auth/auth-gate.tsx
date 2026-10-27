"use client";

import { useEffect, useState } from "react";
import { TradingSystemApp } from "../trading-system-app";
import { LoginPage } from "./login-page";
import { SeeAlphaLogo } from "../brand/seealpha-logo";

export type AuthUser = {
  id: number;
  username: string;
};

type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: AuthUser };

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
        const data = (await response.json()) as { user: AuthUser };
        setAuth({ status: "authenticated", user: data.user });
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
        onAuthenticated={(user) => setAuth({ status: "authenticated", user })}
      />
    );
  }

  return <TradingSystemApp user={auth.user} onLogout={logout} />;
}
