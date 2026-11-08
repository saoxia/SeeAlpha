import type { NextConfig } from "next";
import { fileURLToPath } from "node:url";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

// Hostnames only. Scheme, port, path, and wildcards are rejected so a LAN
// address stays off unless SEEALPHA_ALLOWED_DEV_ORIGINS sets it explicitly.
const allowedDevOrigins = (process.env.SEEALPHA_ALLOWED_DEV_ORIGINS ?? "")
  .split(",")
  .map((item) => item.trim())
  .filter((item) => item.length > 0 && !/[/*:]/.test(item));

const nextConfig: NextConfig = {
  agentRules: false,
  // Native account reconciliation and command acknowledgement can exceed 30s.
  experimental: { proxyTimeout: 90_000 },
  allowedDevOrigins,
  images: {
    unoptimized: true,
    remotePatterns: [],
  },
  output: "standalone",
  async rewrites() {
    const apiUrl = process.env.SEEALPHA_API_URL ?? "http://127.0.0.1:8000";
    return [
      { source: "/api/data/:path*", destination: `${apiUrl}/api/data/:path*` },
      { source: "/api/system/update/:path+", destination: "/api/system/update" },
      { source: "/api/operations/:path*", destination: `${apiUrl}/api/operations/:path*` },
      { source: "/api/auth/:path*", destination: `${apiUrl}/api/auth/:path*` },
      { source: "/api/users", destination: `${apiUrl}/api/users` },
      { source: "/api/users/:path*", destination: `${apiUrl}/api/users/:path*` },
      { source: "/api/tenants", destination: `${apiUrl}/api/tenants` },
      { source: "/api/tenants/:path*", destination: `${apiUrl}/api/tenants/:path*` },
      { source: "/api/trading/:path*", destination: `${apiUrl}/api/trading/:path*` },
      { source: "/api/management/:path*", destination: `${apiUrl}/api/management/:path*` },
      { source: "/api/health", destination: `${apiUrl}/api/health` },
      { source: "/api/system/olap/:path*", destination: `${apiUrl}/api/system/olap/:path*` },
      { source: "/api/clickhouse/:path*", destination: `${apiUrl}/api/clickhouse/:path*` },
      { source: "/api/system/exchange-markets", destination: `${apiUrl}/api/system/exchange-markets` },
      { source: "/api/system/exchange-accounts/:path*", destination: `${apiUrl}/api/system/exchange-accounts/:path*` },
      { source: "/api/exchange-accounts", destination: `${apiUrl}/api/exchange-accounts` },
      { source: "/api/exchange-accounts/equity-candles", destination: `${apiUrl}/api/exchange-accounts/equity-candles` },
      { source: "/api/exchange-accounts/:accountId/equity-candles", destination: `${apiUrl}/api/exchange-accounts/:accountId/equity-candles` },
      { source: "/api/exchange-accounts/portfolio", destination: `${apiUrl}/api/exchange-accounts/portfolio` },
      { source: "/api/exchange-accounts/:accountId/portfolio", destination: `${apiUrl}/api/exchange-accounts/:accountId/portfolio` },
      { source: "/api/exchange-accounts/:accountId/positions", destination: `${apiUrl}/api/exchange-accounts/:accountId/positions` },
    ];
  },
  turbopack: {
    root: projectRoot,
  },
};

export default nextConfig;
