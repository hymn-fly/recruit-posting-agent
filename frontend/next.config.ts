import type { NextConfig } from "next";
const config: NextConfig = {
  compress: false, // gzip이 켜져 있으면 프록시가 SSE를 응답이 끝날 때까지 모아서 보낸다
  experimental: { proxyTimeout: 300000 },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_BASE_URL || "http://127.0.0.1:8000"}/:path*`,
      },
    ];
  },
};
export default config;
