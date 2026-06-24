import path from "path";
const __dirname = import.meta.dirname;

/** @type {import('next').NextConfig} */
const nextConfig = {
  turbopack: {
    // Pin the workspace root to the monorepo root so Turbopack resolves
    // hoisted dependencies correctly and doesn't infer the wrong root.
    root: path.join(__dirname, "..", ".."),
  },
};

export default nextConfig;
