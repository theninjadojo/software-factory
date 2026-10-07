import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The Dockerfile sets BUILD_STANDALONE=1 for a self-contained server in .next/standalone.
  // Without it (local runs, CI, Vercel) the build is the usual one that `next start` serves.
  output: process.env.BUILD_STANDALONE === "1" ? "standalone" : undefined,
};

export default nextConfig;
