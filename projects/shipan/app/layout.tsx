import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "观市 · Market Lens",
  description: "指数与 ETF 行情监控、资金观察与 Agent 读盘工作台。",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}
