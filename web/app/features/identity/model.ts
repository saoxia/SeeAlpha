export type AuthUser = {
  id: number;
  username: string;
};

export type AuthSession = {
  user: AuthUser;
  isAdmin: boolean;
};

export function canAccessAdminConsole(session: AuthSession): boolean {
  return session.isAdmin;
}

export function sessionHomePath(session: AuthSession): "/admin" | "/app" {
  return canAccessAdminConsole(session) ? "/admin" : "/app";
}

export type TenantPurpose = "production" | "development";

export type TenantStatus = "active" | "archived";

export type TenantRecord = {
  id: number;
  owner_id: number;
  purpose: TenantPurpose;
  name: string;
  description: string | null;
  status: TenantStatus;
};

export type ManagedUser = {
  id: number;
  username: string;
  created_at: number;
  production_tenant_id: number;
  production_tenant_name: string;
  engine_node: string | null;
  resources: UserResources;
};

export type ResourceKind = "harness" | "development" | "production";
export type ResourceAllocation = {
  enabled: boolean;
  cpus: string | null;
  memory: string | null;
  node: string | null;
};
export type UserResources = Record<ResourceKind, ResourceAllocation>;
export type ResourceCapabilities = {
  nodes: EngineNode[];
  defaults: Record<ResourceKind, { cpus: string; memory: string }>;
  harness_deployment: "disabled" | "single" | "per_user";
  engine_deployment: "single" | "docker" | "distributed";
};

export type EngineNode = { id: string; online: boolean };
export type EngineNodes = { deployment: "single" | "docker" | "distributed"; nodes: EngineNode[] };

export type AuthPayload = {
  user?: AuthUser;
  is_admin?: boolean;
};

export function sessionFromAuth(data: AuthPayload | null | undefined): AuthSession | null {
  if (!data?.user?.username) return null;
  return {
    user: data.user,
    isAdmin: Boolean(data.is_admin),
  };
}

export function productionTenant<T extends { purpose: string }>(tenants: readonly T[]): T | null {
  return tenants.find((tenant) => tenant.purpose === "production") ?? null;
}

export function sortTenants<T extends { id: number; purpose: string }>(tenants: readonly T[]): T[] {
  return [...tenants].sort((left, right) => {
    if (left.purpose !== right.purpose) return left.purpose === "production" ? -1 : 1;
    return left.id - right.id;
  });
}

export function purposeLabel(purpose: string): string {
  if (purpose === "production") return "正式";
  if (purpose === "development") return "开发";
  return purpose;
}

export function statusLabel(status: string): string {
  if (status === "active") return "使用中";
  if (status === "archived") return "已归档";
  return status;
}

export function developmentCreateBody(name: string, description: string) {
  const body: { name: string; description?: string; purpose: "development" } = {
    name: name.trim(),
    purpose: "development",
  };
  const text = description.trim();
  if (text) body.description = text;
  return body;
}

export function copyTenantBody(name: string) {
  return { name: name.trim() };
}

export function shareTenantBody(name: string, username: string) {
  return { name: name.trim(), username: username.trim() };
}

export function usersPath() {
  return "/api/users";
}

export function tenantsPath() {
  return "/api/tenants";
}

export function tenantActionPath(tenantId: number, action: "archive" | "restore" | "copy" | "share") {
  return `/api/tenants/${tenantId}/${action}`;
}

export function errorDetail(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  return fallback;
}
