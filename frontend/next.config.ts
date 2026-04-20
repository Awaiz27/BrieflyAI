import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        // API_URL is a server-only env var (no NEXT_PUBLIC_ prefix).
        // Set API_URL=http://api:9001 in Docker, keep http://localhost:9001 for local dev.
        destination: `${process.env.API_URL ?? "http://localhost:9001"}/:path*`,
      },
    ];
  },
};

export default nextConfig;
