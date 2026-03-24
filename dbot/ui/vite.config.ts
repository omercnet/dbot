import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          "ai-sdk": ["ai", "@ai-sdk/react"],
          markdown: ["react-markdown", "remark-gfm"],
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:7932",
    },
  },
});
