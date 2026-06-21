# ADR-007: 配置中心化（dotenv 统一加载）

## 状态
Accepted（2026-06-22）

## 上下文

历史上配置加载分散在三处：

- `src/long_earn/__main__.py` 顶部 `load_dotenv()`
- `tests/integration/conftest.py` 顶部 `load_dotenv()`
- `tests/integration/test_develop_backtest.py` 模块级 `load_dotenv()`

加上 `AppConfig.from_env()` 直接读 `os.environ`，导致：

1. **多入口**：CI / 脚本 / Notebook / 集成测试每处都要记得 `load_dotenv()`；漏调即用空环境变量跑出"不符合预期"的默认值。
2. **无多环境支持**：所有调用都是 `load_dotenv()`（默认查 `.env`），无法快速切到 `dev` / `staging` / `prod` 配置——TODO #4.3 的核心诉求。
3. **行为不一致**：默认 `load_dotenv()` 走 `override=False`，但调用方常以为"环境变量被 .env 覆盖"，导致排错混乱。

## 决策

新增 **`long_earn.config.load_config()`** 作为配置加载**唯一推荐入口**，封装：

1. **dotenv 加载**：用 `python-dotenv`（已加入直接依赖），`override=False` 显式约定"`os.environ` 优先于 `.env` 文件"（生产部署的标准做法）。
2. **多环境支持**：通过 `LONG_EARN_ENV` 环境变量选择 `.env.<name>`（如 `dev` / `staging` / `prod`），缺失则回退默认 `.env`。
3. **可注入**：`env_file` 参数允许显式指定路径（测试 / CI 友好），`search_from` 参数控制查找起点（默认项目根）。
4. **`AppConfig.from_env()` 不变**：保留作为底层"从已加载的环境变量构造配置"的纯函数，`load_config()` 是其上层 facade。

`context_init.create_runtime_context()` 在 `config is None` 时改调 `load_config()`，所有下游（包括 `initialize_context()` / 集成测试 / `__main__`）自动走中心化路径。

### 优先级（自上而下）

```
显式 os.environ（生产部署） > 选定的 .env 文件 > AppConfig 默认值
```

### 文件选择优先级

```
显式 env_file 参数 > .env.<LONG_EARN_ENV> > .env > 无文件（用 AppConfig 默认值）
```

## 收益

- **单一入口**：所有调用方只需 `from long_earn.config import load_config; cfg = load_config()`，不再分散 `load_dotenv()`。
- **多环境零成本切换**：`export LONG_EARN_ENV=dev` 一行切换；CI 矩阵可用同一 image 跑不同环境。
- **可测试**：6 项接口契约单测覆盖（默认/自定义文件/`LONG_EARN_ENV` 选择/回退/`os.environ` 优先级/显式 `env_file`）。
- **向后兼容**：`AppConfig.from_env()` / `AppConfig.from_env()` 现有调用全部不变；旧脚本继续工作。

## 替代方案

- **`config.yaml` 替代 `.env`**：考虑过引入 yaml 多环境支持，但项目已有 `.env` 文件 + 工具链（IDE/CI/Docker 普遍支持 dotenv），引入 yaml 反而增加心智负担。**dotenv + 多文件命名约定**已满足"多环境配置"诉求。
- **Pydantic Settings**：功能更强（带类型校验/嵌套），但当前 `AppConfig` 只有 9 个扁平字段，引入 pydantic-settings 会增加依赖且重写 `AppConfig`，性价比低。可作为未来扩展项（若字段数量大幅增长再考虑）。

## 影响

- 新增直接依赖：`python-dotenv>=1.0.0`（已是 `langchain` 的间接依赖，引入零额外体积）。
- 修改文件：`config.py`（新增 `load_config` / `_resolve_env_file`），`context_init.py`（默认走 `load_config`），`__main__.py` / `tests/integration/conftest.py` / `test_develop_backtest.py`（移除 ad-hoc `load_dotenv`）。
- 新增测试：`tests/unit/test_config.py::TestLoadConfig` 6 用例。
