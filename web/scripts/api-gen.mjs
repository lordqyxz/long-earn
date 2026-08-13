/**
 * 从运行中的 FastAPI 后端拉取 OpenAPI spec 并生成类型化 TS API 客户端。
 *
 * 用法：npm run api:gen
 * 前置：后端 FastAPI 服务已在 localhost:8090 运行（可用 OPENAPI_URL 覆盖地址）。
 * 产物：src/api/（client.gen.ts / types.gen.ts / sdk.gen.ts / index.ts），禁止手改。
 */
import { execSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const openapiUrl = process.env.OPENAPI_URL || 'http://localhost:8090/openapi.json'
const outFile = path.join(root, 'openapi.json')

async function main() {
  const res = await fetch(openapiUrl)
  if (!res.ok) {
    throw new Error(
      `无法获取 OpenAPI spec (HTTP ${res.status}): ${openapiUrl}\n` +
        '请确认后端 FastAPI 服务已启动（uv run python -m long_earn --fastapi）',
    )
  }
  const spec = await res.json()
  writeFileSync(outFile, JSON.stringify(spec, null, 2), 'utf-8')
  console.log(`已拉取 OpenAPI spec → ${path.relative(root, outFile)}`)

  execSync('npx openapi-ts', { cwd: root, stdio: 'inherit' })
  console.log('API 客户端已生成 → src/api/')
}

main().catch((e) => {
  console.error(e.message)
  process.exit(1)
})
