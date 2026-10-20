import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // main.py 默认 8001，`uv run knowledge-mcp` 默认 8000，可用 VITE_BACKEND_URL 覆盖
  const target = env.VITE_BACKEND_URL || 'http://127.0.0.1:8000'
  const proxy = { target, changeOrigin: true }
  return {
    plugins: [vue()],
    server: {
      port: 5173,
      proxy: { '/mcp': proxy, '/api': proxy, '/health': proxy }
    },
    build: { outDir: 'dist', emptyOutDir: true }
  }
})
