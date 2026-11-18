"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { TradingSystemApp } from "../trading-system-app";
import { AdminConsole } from "../admin/admin-console";
import { LoginPage } from "./login-page";
import { SeeAlphaLogo } from "../brand/seealpha-logo";
import {
  canAccessAdminConsole, sessionFromAuth, sessionHomePath, type AuthSession,
} from "../../features/identity/model";

export type { AuthUser } from "../../features/identity/model";

type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; session: AuthSession };

type Destination = "auto" | "app" | "admin";

export function AuthGate({ destination = "auto" }: { destination?: Destination }) {
  const router = useRouter();
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

  useEffect(() => {
    if (auth.status !== "authenticated") return;
    if (destination === "auto") {
      router.replace(sessionHomePath(auth.session));
    } else if (destination === "admin" && !canAccessAdminConsole(auth.session)) {
      router.replace("/app");
    } else if (destination === "app" && canAccessAdminConsole(auth.session)) {
      router.replace("/admin");
    }
  }, [auth, destination, router]);

  async function logout() {
    const response = await fetch("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    });
    if (!response.ok) throw new Error("退出失败");
    setAuth({ status: "anonymous" });
    router.replace("/");
  }



  if (auth.status === "loading" ||
      (auth.status === "authenticated" &&
        (destination === "auto" ||
         (destination === "admin" && !canAccessAdminConsole(auth.session)) ||
         (destination === "app" && canAccessAdminConsole(auth.session))))) {
    return (
      <main className="auth-loading" aria-label="正在检查登录状态">
        <SeeAlphaLogo className="loading-brand" />
        <p>正在连接交易系统…</p>
      </main>
    );
  }

  if (auth.status === "anonymous") {
    return <LoginPage onAuthenticated={(session) => setAuth({ status: "authenticated", session })} />;
  }

  if (destination === "admin") {
    return <AdminConsole session={auth.session} onLogout={logout} />;
  }

  return (
    <TradingSystemApp session={auth.session} onLogout={logout} />
  );
}
