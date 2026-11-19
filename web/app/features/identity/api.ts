import {
  errorDetail,
  tenantActionPath,
  tenantsPath,
  usersPath,
} from "./model";
import type { EngineNodes, ManagedUser, ResourceCapabilities, TenantCapabilities, TenantRecord, UserResources } from "./model";

export {
  tenantActionPath,
  tenantsPath,
  usersPath,
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    ...init,
    headers,
  });
  if (response.status === 204) return undefined as T;
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorDetail(payload, "请求失败，请稍后重试。"));
  return payload as T;
}

export function fetchTenants(signal?: AbortSignal) {
  return request<TenantRecord[]>(tenantsPath(), { signal });
}

export function fetchTenantCapabilities(signal?: AbortSignal) {
  return request<TenantCapabilities>("/api/tenants/capabilities", { signal });
}

export function createDevelopmentTenant(body: { name: string; description?: string; purpose: "development" }) {
  return request<TenantRecord>(tenantsPath(), { method: "POST", body: JSON.stringify(body) });
}

export function archiveTenant(tenantId: number) {
  return request<TenantRecord>(tenantActionPath(tenantId, "archive"), { method: "POST" });
}

export function restoreTenant(tenantId: number) {
  return request<TenantRecord>(tenantActionPath(tenantId, "restore"), { method: "POST" });
}

export function deleteTenant(tenantId: number) {
  return request<void>(`/api/tenants/${tenantId}`, { method: "DELETE" });
}

export function copyTenant(tenantId: number, body: { name: string }) {
  return request<TenantRecord>(tenantActionPath(tenantId, "copy"), {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function shareTenant(tenantId: number, body: { name: string; username: string }) {
  return request<TenantRecord>(tenantActionPath(tenantId, "share"), {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function fetchUsers(signal?: AbortSignal) {
  return request<ManagedUser[]>(usersPath(), { signal });
}

export function createUser(body: { username: string; password: string }) {
  return request<ManagedUser>(usersPath(), { method: "POST", body: JSON.stringify(body) });
}

export function fetchEngineNodes() {
  return request<EngineNodes>("/api/users/engine-nodes");
}

export function deployUserEngine(userId: number, nodeId: string) {
  return request<ManagedUser>(`/api/users/${userId}/engine`, {
    method: "POST", body: JSON.stringify({ node_id: nodeId }),
  });
}


export function fetchResourceCapabilities() {
  return request<ResourceCapabilities>("/api/users/resource-capabilities");
}

export function saveUserResources(userId: number, resources: UserResources) {
  return request<ManagedUser>("/api/users/" + userId + "/resources", {
    method: "PUT", body: JSON.stringify(resources),
  });
}
