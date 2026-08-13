import { defineConfig } from '@hey-api/openapi-ts'

export default defineConfig({
  input: 'openapi.json',
  output: 'src/api',
  plugins: [
    '@hey-api/typescript',
    '@hey-api/sdk',
    // baseUrl 为空字符串：请求走相对路径（开发走 Vite 代理，生产由 FastAPI 同源托管）
    { name: '@hey-api/client-fetch', baseUrl: '' },
  ],
})
