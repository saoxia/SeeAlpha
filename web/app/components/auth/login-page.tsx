"use client";

import { FormEvent, useState } from "react";
import { sessionFromAuth, type AuthSession } from "../../features/identity/model";
import { SeeAlphaLogo } from "../brand/seealpha-logo";

type LoginPageProps = {
  onAuthenticated: (session: AuthSession) => void;
};

export function LoginPage({ onAuthenticated }: LoginPageProps) {
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setPending(true);
    const form = new FormData(event.currentTarget);

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          username: form.get("username"),
          password: form.get("password"),
        }),
      });
      const data = (await response.json()) as {
        user?: { id: number; username: string };
        actor?: { id: number; username: string } | null;
        impersonating?: boolean;
        is_admin?: boolean;
        detail?: unknown;
      };
      const session = sessionFromAuth(data);
      if (!response.ok || !session) {
        setError(
          typeof data.detail === "string" ? data.detail : "登录失败，请稍后再试。",
        );
        return;
      }
      onAuthenticated(session);
    } catch {
      setError("无法连接登录服务，请稍后再试。");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-intro" aria-label="SeeAlpha 产品介绍">
        <SeeAlphaLogo className="login-brand" inverse />
        <p className="login-kicker">AI-NATIVE TRADING INTELLIGENCE</p>
        <h1>看见信号，<br />交易 Alpha。</h1>
        <p className="login-description">
          一个面向 AI 策略、实时行情、仓位管理与数据运维的统一交易系统。
        </p>
        <small>PRIVATE SYSTEM · AUTHORIZED ACCESS ONLY</small>
      </section>

      <section className="login-panel">
        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-form-heading">
            <h2>登录 SeeAlpha</h2>
          </div>

          <label>
            <span>用户名</span>
            <input
              name="username"
              type="text"
              autoComplete="username"
              required
              maxLength={64}
            />
          </label>
          <label>
            <span>密码</span>
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
              maxLength={256}
            />
          </label>

          <div className="login-message" role="alert" aria-live="polite">
            {error}
          </div>
          <button type="submit" disabled={pending}>
            {pending ? "正在验证…" : "进入系统 →"}
          </button>
        </form>
      </section>
    </main>
  );
}
