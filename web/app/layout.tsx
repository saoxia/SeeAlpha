import type { Metadata } from "next";
import "./globals.css";
import "./styles/features/live.css";

export const metadata: Metadata = {
  title: "SeeAlpha — AI 交易系统",
  description: "集行情观察、策略分析、交易管理与数据运维于一体的 AI 交易系统。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
