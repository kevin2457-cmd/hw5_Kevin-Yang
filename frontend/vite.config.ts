import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 前端直接请求 http://localhost:8000 的 FastAPI 后端（backend/main.py 已为 5173 配置跨域）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
})
