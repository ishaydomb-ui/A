import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Ships a self-contained server with only the files it actually needs,
  // which keeps the deployed image small.
  output: "standalone",
  // Native / optional modules the bundler should leave alone. Playwright is a
  // dev dependency and is absent in production: the browser worker imports it
  // dynamically and only when explicitly enabled, so it must not be bundled.
  serverExternalPackages: ["better-sqlite3", "playwright"],
};

export default nextConfig;
