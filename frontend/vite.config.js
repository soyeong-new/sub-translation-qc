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
      // 백엔드가 /media/chart_image, /media/video_proxy를 정적 파일로 서빙한다
      // (app/main.py). GET /titles/{id}가 내려주는 chart_image_url은 /api 접두사가
      // 없는 절대경로(/media/...)이므로, 이 경로도 별도로 백엔드에 프록시해야
      // 개발 서버에서 이미지가 실제로 로드된다 — 안 그러면 Vite의 SPA fallback이
      // index.html을 대신 돌려줘 이미지가 깨진다.
      "/media": {
        target: "http://localhost:8000",
      },
    },
  },
});
