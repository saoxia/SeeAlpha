"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  archiveTenant,
  copyTenant,
  createDevelopmentTenant,
  deleteTenant,
  fetchTenants,
  restoreTenant,
  shareTenant,
} from "../../features/identity/api";
import {
  copyTenantBody,
  developmentCreateBody,
  productionTenant,
  purposeLabel,
  shareTenantBody,
  sortTenants,
  statusLabel,
  type TenantRecord,
} from "../../features/identity/model";

type Section = "list" | "create" | "copy" | "share";

const sections: Array<{ id: Section; number: string; label: string; description: string }> = [
  { id: "list", number: "01", label: "我的租户", description: "正式租户为默认" },
  { id: "create", number: "02", label: "新建开发租户", description: "用于回测和试策略" },
  { id: "copy", number: "03", label: "复制配置", description: "订阅复制到新开发租户" },
  { id: "share", number: "04", label: "分享配置", description: "对方获得开发租户" },
];

export function TenantPage() {
  const [section, setSection] = useState<Section>("list");
  const [tenants, setTenants] = useState<TenantRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setTenants(sortTenants(await fetchTenants()));
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取租户。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const rows = sortTenants(await fetchTenants());
        if (!active) return;
        setTenants(rows);
        setError("");
      } catch (caught) {
        if (!active) return;
        setError(caught instanceof Error ? caught.message : "无法读取租户。");
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    return () => {
      active = false;
    };
  }, []);

  const production = productionTenant(tenants);

  async function run(action: () => Promise<void>, success: string) {
    setBusy(true);
    setNotice("");
    setError("");
    try {
      await action();
      await refresh();
      setNotice(success);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作失败。");
    } finally {
      setBusy(false);
    }
  }

  function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const body = developmentCreateBody(String(data.get("name") ?? ""), String(data.get("description") ?? ""));
    void run(async () => {
      await createDevelopmentTenant(body);
      form.reset();
      setSection("list");
    }, "已创建开发租户。");
  }

  function onCopy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const tenantId = Number(data.get("source"));
    const body = copyTenantBody(String(data.get("name") ?? ""));
    void run(async () => {
      await copyTenant(tenantId, body);
      form.reset();
      setSection("list");
    }, "已复制为新的开发租户。交易所密钥和虚拟账户资金没有复制。");
  }

  function onShare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const tenantId = Number(data.get("source"));
    const body = shareTenantBody(String(data.get("name") ?? ""), String(data.get("username") ?? ""));
    void run(async () => {
      await shareTenant(tenantId, body);
      form.reset();
      setSection("list");
    }, "已分享。对方收到的是自己的开发租户。");
  }

  return (
    <section className="settings-page" id="tenants">
      <div className="settings-layout">
        <aside className="settings-sidebar" aria-label="租户目录">
          <div className="settings-sidebar-head">
            <span>TENANTS</span>
            <b>租户</b>
          </div>
          <nav>
            {sections.map((item) => (
              <button
                key={item.id}
                type="button"
                className={section === item.id ? "active" : ""}
                aria-current={section === item.id ? "page" : undefined}
                onClick={() => setSection(item.id)}
              >
                <span>{item.number}</span>
                <div>
                  <b>{item.label}</b>
                  <small>{item.description}</small>
                </div>
                <i aria-hidden="true">→</i>
              </button>
            ))}
          </nav>
        </aside>
        <main className="settings-content">
          <div className="settings-card">
            <header className="settings-card-head">
              <span>{sections.find((item) => item.id === section)?.number}</span>
              <div>
                <h2>{sections.find((item) => item.id === section)?.label}</h2>
                <p>
                  日常功能默认使用正式租户{production ? `「${production.name}」` : ""}。
                  开发工作只在开发租户上进行。
                </p>
              </div>
            </header>
            {error && <p className="settings-feedback error" role="alert">{error}</p>}
            {notice && <p className="settings-feedback success" role="status">{notice}</p>}
            {section === "list" && (
              <TenantList
                tenants={tenants}
                loading={loading}
                busy={busy}
                onArchive={(tenant) => void run(() => archiveTenant(tenant.id).then(() => undefined), "已归档。")}
                onRestore={(tenant) => void run(() => restoreTenant(tenant.id).then(() => undefined), "已恢复。")}
                onDelete={(tenant) => {
                  if (!window.confirm(`确定删除开发租户「${tenant.name}」？此操作不可恢复。`)) return;
                  void run(() => deleteTenant(tenant.id), "已删除。");
                }}
              />
            )}
            {section === "create" && (
              <form className="form-grid" onSubmit={onCreate}>
                <label className="field">
                  <span>名称</span>
                  <input name="name" required maxLength={100} placeholder="例如：ETH 研究" />
                </label>
                <label className="field">
                  <span>说明</span>
                  <input name="description" maxLength={500} placeholder="可选" />
                </label>
                <p className="field field-wide github-key-note">
                  新建的只能是开发租户。每个用户已经有且只能有一个正式租户。
                </p>
                <div className="field field-wide">
                  <button className="monitor-add-button" type="submit" disabled={busy}>
                    {busy ? "正在创建…" : "创建开发租户"}
                  </button>
                </div>
              </form>
            )}
            {section === "copy" && (
              <form className="form-grid" onSubmit={onCopy}>
                <SourceField tenants={tenants} />
                <label className="field">
                  <span>新开发租户名称</span>
                  <input name="name" required maxLength={100} />
                </label>
                <p className="field field-wide github-key-note">
                  只复制行情订阅（品种和数据区间）。交易所密钥不会复制。虚拟账户和初始资金也不会复制，需要在新租户里重新设置。
                </p>
                <div className="field field-wide">
                  <button className="monitor-add-button" type="submit" disabled={busy || tenants.length === 0}>
                    {busy ? "正在复制…" : "复制为开发租户"}
                  </button>
                </div>
              </form>
            )}
            {section === "share" && (
              <form className="form-grid" onSubmit={onShare}>
                <SourceField tenants={tenants} />
                <label className="field">
                  <span>对方用户名</span>
                  <input name="username" required maxLength={64} autoComplete="off" />
                </label>
                <label className="field field-wide">
                  <span>对方的新开发租户名称</span>
                  <input name="name" required maxLength={100} />
                </label>
                <p className="field field-wide github-key-note">
                  对方会得到一份自己的开发租户，不会成为第二个正式租户。密钥和虚拟账户资金不会带过去。
                </p>
                <div className="field field-wide">
                  <button className="monitor-add-button" type="submit" disabled={busy || tenants.length === 0}>
                    {busy ? "正在分享…" : "分享配置"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </main>
      </div>
    </section>
  );
}

function SourceField({ tenants }: { tenants: TenantRecord[] }) {
  const production = productionTenant(tenants);
  return (
    <label className="field">
      <span>来源租户</span>
      <select
        key={tenants.map((tenant) => tenant.id).join(",")}
        name="source"
        required
        defaultValue={production ? String(production.id) : ""}
      >
        {tenants.map((tenant) => (
          <option key={tenant.id} value={tenant.id}>
            {purposeLabel(tenant.purpose)} · {tenant.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function TenantList({
  tenants,
  loading,
  busy,
  onArchive,
  onRestore,
  onDelete,
}: {
  tenants: TenantRecord[];
  loading: boolean;
  busy: boolean;
  onArchive: (tenant: TenantRecord) => void;
  onRestore: (tenant: TenantRecord) => void;
  onDelete: (tenant: TenantRecord) => void;
}) {
  if (loading) return <p className="exchange-empty">正在读取租户…</p>;
  if (tenants.length === 0) return <p className="exchange-empty">还没有租户。</p>;
  return (
    <div className="tenant-list">
      {tenants.map((tenant) => (
        <article key={tenant.id} className="tenant-row">
          <div>
            <b>{tenant.name}</b>
            <small>{tenant.description || (tenant.purpose === "production" ? "默认正式租户" : "开发租户")}</small>
          </div>
          <span className={tenant.purpose === "production" ? "tenant-badge production" : "tenant-badge"}>
            {purposeLabel(tenant.purpose)}
          </span>
          <span>{statusLabel(tenant.status)}</span>
          <div className="tenant-actions">
            {tenant.purpose === "development" && tenant.status === "active" && (
              <button type="button" disabled={busy} onClick={() => onArchive(tenant)}>归档</button>
            )}
            {tenant.purpose === "development" && tenant.status === "archived" && (
              <button type="button" disabled={busy} onClick={() => onRestore(tenant)}>恢复</button>
            )}
            {tenant.purpose === "development" && (
              <button type="button" className="remove" disabled={busy} onClick={() => onDelete(tenant)}>删除</button>
            )}
            {tenant.purpose === "production" && <small>不可删除</small>}
          </div>
        </article>
      ))}
    </div>
  );
}
