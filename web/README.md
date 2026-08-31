# long-earn-web

Long Earn 量化交易平台的前端（React 18 + Vite + TypeScript + Tailwind + Radix UI + Recharts）。

## 技术栈

- **React 18** + **react-router-dom** 6（多页面 SPA）
- **Vite 5** + TypeScript（`@/` 别名指向 `src/`）
- **Tailwind CSS 3** + shadcn/ui 风格组件（`src/components/ui/`）
- **Recharts**（权益曲线、个股价格图）
- **@hey-api/openapi-ts**（从后端 FastAPI OpenAPI 自动生成类型化 API 客户端）

## 目录结构

```
web/
├── components.json          # shadcn/ui 配置
├── vite.config.ts           # 开发代理：/api、/ws → localhost:8090
└── src/
    ├── api/                 # openapi-ts 生成的客户端（勿手改，运行 api:gen 重新生成）
    ├── components/
    │   ├── ui/              # shadcn 原语 + CollapsibleSection
    │   ├── dashboard/       # 回测看板：编排 + trades/ audit/ charts/
    │   ├── research/        # 策略研发
    │   └── event-flow/      # 事件分析（Trigger / Stats / Table / Log）
    ├── hooks/               # 一文件一 hook；WS 与 REST 分开
    ├── lib/                 # cn / 格式化 / wsReconnect
    ├── pages/               # 路由组合面
    └── types/               # OpenAPI 覆盖不到的 WS 类型（events-ws / research）
```

## 开发

```sh
npm install
npm run dev        # 启动 Vite dev server（端口 5173，代理到后端 8090）
```

后端 FastAPI 服务（`uv run python -m long_earn web`，默认端口 8090）需先行启动，
前端通过 Vite 代理访问 `/api` 与 `/ws`。

## API 客户端（自动生成）

后端所有 REST 端点声明了 Pydantic `response_model`，前端据此自动生成类型化客户端：

```sh
npm run api:gen    # 需要后端已在 localhost:8090 运行
```

- 读取 `http://localhost:8090/openapi.json` 生成到 `src/api/`
- 生成产物（`client.gen.ts` / `types.gen.ts` / `sdk.gen.ts` / `index.ts`）**禁止手改**
- 后端接口变更后重新运行 `npm run api:gen` 即可
- WebSocket（`/ws/events`、`/ws/research`）不在 OpenAPI 范围内，为手写实现（`src/hooks/`）

## 构建

```sh
npm run build      # tsc -b && vite build，产物在 dist/
```

后端生产模式直接挂载 `web/dist/` 提供 SPA 页面（见 `fastapi_app.py`）。
