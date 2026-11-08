"use client";

import { useState } from "react";
import { stopImpersonation } from "../../features/identity/api";
import { bannerText, type AuthSession } from "../../features/identity/model";

type ImpersonationBannerProps = {
  username: string;
  onSession: (session: AuthSession) => void;
};

export function ImpersonationBanner({ username, onSession }: ImpersonationBannerProps) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function stop() {
    setPending(true);
    setError("");
    try {
      onSession(await stopImpersonation());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法返回自己的视图。");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="impersonation-banner" role="status" aria-live="polite">
      <p>
        <b>{bannerText(username)}</b>
        {error ? <span>{error}</span> : <span>当前操作按该用户的权限执行。</span>}
      </p>
      <button type="button" onClick={() => void stop()} disabled={pending}>
        {pending ? "正在返回…" : "返回我的视图"}
      </button>
    </div>
  );
}
