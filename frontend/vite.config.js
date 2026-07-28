import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // 백엔드(Tasks 15-20)는 /api 접두사 없이 라우트를 등록하므로
      // (예: POST /titles), 프록시에서 /api 접두사를 제거해 전달한다.
      "/api": {
        target: "http://localhost:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
