export type AuthUser = {
  id: number;
  username: string;
};

export type AuthSession = {
  user: AuthUser;
  actor: AuthUser | null;
  impersonating: boolean;
  isAdmin: boolean;
};

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
};

export type AuthPayload = {
  user?: AuthUser;
  actor?: AuthUser | null;
  impersonating?: boolean;
  is_admin?: boolean;
};

export function sessionFromAuth(data: AuthPayload | null | undefined): AuthSession | null {
  if (!data?.user?.username) return null;
  return {
    user: data.user,
    actor: data.actor ?? null,
    impersonating: Boolean(data.impersonating),
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

export function bannerText(username: string): string {
  return `正在查看「${username}」的视图`;
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

export function impersonatePath() {
  return "/api/auth/impersonate";
}

export function stopImpersonationPath() {
  return "/api/auth/impersonate/stop";
}

export function errorDetail(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  return fallback;
}
