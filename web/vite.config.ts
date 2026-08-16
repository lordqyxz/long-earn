import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    // 忽略生成工具（openapi-ts 等）的临时文件/临时目录：Windows 上这些文件
    // 原子替换时会被短暂锁住，Vite watcher watch 到会抛 EBUSY 直接崩溃
    watch: {
      ignored: ['**/*.tmp', '**/.*.tmpdir/**'],
    },
    proxy: {
      '/api': 'http://localhost:8090',
      '/ws': {
        target: 'ws://localhost:8090',
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})