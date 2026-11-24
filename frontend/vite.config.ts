import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || "http://localhost:8000";
  const apiProxySecure = env.VITE_API_PROXY_SECURE !== "false";

  console.info(`[vite] API proxy target: ${apiProxyTarget}`);

  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@": "/src",
      },
    },
    server: {
      port: Number(env.VITE_DEV_PORT || 3000),
      proxy: {
        "/api": {
          target: apiProxyTarget,
          changeOrigin: true,
          secure: apiProxySecure,
          ws: true,
        },
      },
    },
    build: {
      outDir: "dist",
      sourcemap: false,
      minify: "esbuild",
      rollupOptions: {
        output: {
          manualChunks: {
            "react-vendor": ["react", "react-dom", "react-router-dom"],
            "antd-vendor": ["antd", "@ant-design/icons"],
            "charts-vendor": ["recharts"],
            "utils-vendor": ["axios", "dayjs"],
          },
        },
      },
      chunkSizeWarningLimit: 1000,
    },
  };
});
