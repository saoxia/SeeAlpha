"use client";

import { useEffect, useState } from "react";
import type {
  ManagedUser, ResourceAllocation, ResourceCapabilities, ResourceKind, UserResources,
} from "../../features/identity/model";

const descriptions: Array<{ id: ResourceKind; title: string; subtitle: string }> = [
  { id: "harness", title: "AI Harness", subtitle: "独立的 AI 研究会话与工作空间" },
  { id: "development", title: "开发 Docker", subtitle: "策略开发、测试及回测" },
  { id: "production", title: "正式 Docker", subtitle: "正式策略与交易执行环境" },
];

type Props = {
  user: ManagedUser;
  capabilities: ResourceCapabilities;
  busy: boolean;
  onClose: () => void;
  onSave: (resources: UserResources) => Promise<void>;
  onDeploy: (nodeId: string) => Promise<void>;
};

export function UserResourcesDialog({
  user, capabilities, busy, onClose, onSave, onDeploy,
}: Props) {
  const [draft, setDraft] = useState<UserResources>(() => structuredClone(user.resources));
  const [expanded, setExpanded] = useState<ResourceKind | null>(null);
  const [productionNode, setProductionNode] = useState(user.engine_node ?? "");
  const dirty = JSON.stringify(draft) !== JSON.stringify(user.resources);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) {
        if (dirty && !window.confirm("尚未保存修改，确定关闭吗？")) return;
        onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, dirty, onClose]);

  function change(kind: ResourceKind, values: Partial<ResourceAllocation>) {
    setDraft((current) => ({
      ...current, [kind]: { ...current[kind], ...values },
    }));
  }

  function close() {
    if (busy) return;
    if (dirty && !window.confirm("尚未保存修改，确定关闭吗？")) return;
    onClose();
  }

  const hasEngine = capabilities.engine_deployment !== "single";

  return (
    <div className="admin-resource-overlay" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) close();
    }}>
      <section className="admin-resource-dialog" role="dialog" aria-modal="true"
        aria-labelledby="admin-resource-title">
        <header className="admin-resource-head">
          <div>
            <h2 id="admin-resource-title">权限与资源配置</h2>
            <p>{user.username} · 用户 ID {user.id}</p>
          </div>
          <button type="button" className="admin-resource-close"
            aria-label="关闭资源配置" disabled={busy} onClick={close}>×</button>
        </header>
        <div className="admin-resource-body">
          <p className="admin-resource-intro">
            为用户分配使用资格。资源配额默认继承系统配置；需要覆盖时再展开编辑。
          </p>
          {descriptions.map(({ id, title, subtitle }) => {
            const resource = draft[id];
            const custom = resource.cpus !== null || resource.memory !== null;
            const defaultConfig = capabilities.defaults[id];
            const nodeEditable = (id === "harness" && capabilities.harness_deployment === "per_user")
              || (id === "development" && capabilities.engine_deployment === "distributed");
            return (
              <section key={id} className="admin-resource-card">
                <div className="admin-resource-summary">
                  <div className="admin-resource-description">
                    <strong>{title}</strong>
                    <small>{subtitle}</small>
                    <span>{resource.enabled
                      ? "已分配 · " + (custom ? "自定义配额" : "继承系统默认")
                      : "未分配"}</span>
                  </div>
                  {resource.enabled && (
                    <button type="button" className="admin-resource-text-button"
                      aria-expanded={expanded === id} aria-controls={"resource-fields-" + id}
                      onClick={() => setExpanded(expanded === id ? null : id)}>
                      {expanded === id ? "收起参数" : "编辑参数"}
                    </button>
                  )}
                  <label className="admin-resource-toggle-label">
                    <input type="checkbox" checked={resource.enabled} disabled={busy}
                      onChange={(event) => {
                        change(id, { enabled: event.target.checked });
                        if (!event.target.checked && expanded === id) setExpanded(null);
                      }} />
                    <span>{resource.enabled ? "已分配" : "分配"}</span>
                  </label>
                </div>
                {resource.enabled && expanded === id && (
                  <div className="admin-resource-fields" id={"resource-fields-" + id}>
                    <label className="admin-resource-custom">
                      <input type="checkbox" checked={custom} disabled={busy}
                        onChange={(event) => change(id, event.target.checked
                          ? { cpus: defaultConfig.cpus, memory: defaultConfig.memory }
                          : { cpus: null, memory: null })} />
                      自定义 CPU / 内存配额
                    </label>
                    <div className="admin-resource-field-grid">
                      <label>
                        <span>CPU 核数</span>
                        <input disabled={!custom || busy} inputMode="decimal"
                          aria-label={title + " CPU 核数"}
                          value={resource.cpus ?? defaultConfig.cpus}
                          onChange={(event) => change(id, { cpus: event.target.value })} />
                      </label>
                      <label>
                        <span>内存上限</span>
                        <input disabled={!custom || busy} aria-label={title + " 内存上限"}
                          placeholder="例如 4g" value={resource.memory ?? defaultConfig.memory}
                          onChange={(event) => change(id, { memory: event.target.value })} />
                      </label>
                      {nodeEditable && (
                        <label>
                          <span>部署节点</span>
                          <select value={resource.node ?? ""} disabled={busy}
                            onChange={(event) => change(id, { node: event.target.value || null })}>
                            <option value="">继承系统节点策略</option>
                            {capabilities.nodes.map((node) => (
                              <option key={node.id} value={node.id} disabled={!node.online}>
                                {node.id === "local" ? "主服务器（本机）" : node.id}
                                {node.online ? "" : "（离线）"}
                              </option>
                            ))}
                          </select>
                        </label>
                      )}
                    </div>
                    <p className="admin-resource-policy-note">
                      {id === "production"
                        ? "正式实例的配额变更需要受控停机；节点由下方独立部署操作指定。"
                        : "已有实例不支持直接修改规格或迁移节点；请先按安全流程处理原实例。"}
                    </p>
                  </div>
                )}
              </section>
            );
          })}
          <div className="admin-resource-deployment">
            <div>
              <strong>正式 Docker 部署</strong>
              <p>{user.engine_node
                ? "已绑定节点 " + user.engine_node + "。如启动失败，可重试原节点。"
                : "分配权限不等于部署。首次部署必须单独选择节点并确认。"}</p>
            </div>
            <div className="admin-resource-deploy-controls">
              <select aria-label="正式 Docker 部署节点" value={productionNode}
                disabled={busy || !hasEngine || Boolean(user.engine_node)}
                onChange={(event) => setProductionNode(event.target.value)}>
                <option value="">选择服务器</option>
                {capabilities.nodes.map((node) => (
                  <option key={node.id} value={node.id} disabled={!node.online}>
                    {node.id === "local" ? "主服务器（本机）" : node.id}
                  </option>
                ))}
              </select>
              <button type="button" disabled={busy || dirty || !hasEngine
                || !draft.production.enabled || !productionNode}
                onClick={() => void onDeploy(productionNode)}>
                {user.engine_node ? "重试部署" : "确认部署"}
              </button>
            </div>
            {dirty && <small>请先保存资源权限后再部署。</small>}
          </div>
        </div>
        <footer className="admin-resource-footer">
          <span>{dirty ? "有尚未保存的变更" : "当前配置已保存"}</span>
          <div>
            <button type="button" disabled={busy} onClick={close}>关闭</button>
            <button type="button" className="admin-resource-save"
              disabled={busy || !dirty} onClick={() => void onSave(draft)}>
              {busy ? "处理中…" : "保存配置"}
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}
