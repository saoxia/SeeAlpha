import type { NextConfig } from "next";
import { fileURLToPath } from "node:url";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

const nextConfig: NextConfig = {
  agentRules: false,
  allowedDevOrigins: ["127.0.0.1", "192.168.31.81"],
  output: "standalone",
  async rewrites() {
    const apiUrl = process.env.SEEALPHA_API_URL ?? "http://127.0.0.1:8000";
    return [
      { source: "/api/data/:path*", destination: `${apiUrl}/api/data/:path*` },
      { source: "/api/system/update/:path+", destination: "/api/system/update" },
      { source: "/api/operations/:path*", destination: `${apiUrl}/api/operations/:path*` },
      { source: "/api/auth/:path*", destination: `${apiUrl}/api/auth/:path*` },
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
