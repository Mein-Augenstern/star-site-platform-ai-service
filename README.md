# star-site-platform-ai-service（AI 智能体服务）

平台唯一的 LLM 出口与智能体运行时：prompt 资产、模型接入、AI 能力端点集中于此。
技术栈：**Python 3.11 + FastAPI + LangChain(LCEL) + LangGraph**（业界主流智能体研发框架），模型走智谱 GLM 的 OpenAI 兼容端点（模型无关，换 base_url/model 即可切其他厂商）。

## 能力清单（/internal/ai/**，统一平台 R 信封）

| 端点 | 入参 | 出参 | 消费方 |
|---|---|---|---|
| POST /internal/ai/report-ocr | imageBase64（data URL） | OcrDraft（强类型草稿） | health 拍照识别 |
| POST /internal/ai/report-interpret | 结构化报告+指标 | 解读文本 | health 报告解读 |
| POST /internal/ai/finance-diagnosis | 三张表+收支快照 | 诊断建议文本 | finance 财务诊断 |
| POST /internal/ai/chat | 消息+历史 | 助手回复 | 预留 C 端智能体（/api/ai/**） |

## 教学文档（docs/）

初学者按序阅读即可掌握本服务的完整逻辑：

| 篇目 | 内容 |
|---|---|
| [01-架构总览](docs/01-架构总览.md) | 为什么拆独立 AI 服务、包划分与分层规则、Nacos/R 信封/沙箱三机制 |
| [02-请求执行流程](docs/02-请求执行流程.md) | 三条链路拆解：服务间能力调用 / 智能体 SSE 对话 / 工具查真实数据 |
| [03-智能体与工具](docs/03-智能体与工具.md) | ReAct 循环、@tool 协议（docstring 即说明书）、ContextVar 身份注入、多轮记忆 |
| [04-配置与部署](docs/04-配置与部署.md) | 环境变量全表、换模型厂商操作路径、Docker 运行、排障速查与踩坑记录 |

## 框架形态（大厂标准智能体架构）

- **能力层 = LangChain LCEL 链**（ChatPromptTemplate → ChatOpenAI → 解析器），OCR 用多模态消息 + 宽容 JSON 解析出强类型 pydantic 草稿；
- **智能体层 = LangGraph prebuilt ReAct**（`create_react_agent` + state_modifier 系统提示 + MemorySaver 线程记忆）：
  模型自主决策调用工具查真实家庭数据（工具经 Nacos 服务发现调 Java /internal/** 端点，
  以 JWT 验签出的 memberId 收口归属，模型无法伪造身份），回答后可继续多轮追问（conversation_id 隔离记忆）；
- **SSE 流式**：`POST /api/ai/agent/chat` 逐 token 下发（event: status=工具进度 / token=增量 / done）；
  `POST /api/ai/agent/ask` 非流式一次性回答；
- **工具集**（app/tools，LangChain @tool）：list_family_members / list_recent_reports / get_report_detail（health）、get_balance_sheet（finance）；
- **沙箱模式**：未配置 `AI_LLM_API_KEY` 或 `AI_MOCK_ENABLED=true` 时，智能体降级为 FakeListChatModel 简化图（输出带【沙箱模拟】标记），能力端点返回模拟结果——无密钥环境全链路可用；
- **服务发现**：Nacos open API 注册实例 + 5 秒心跳（httpx 实现，不依赖 SDK；坑：心跳 200 但 body code=20404 须客户端重注册）。

目录：`app/core`（config/security/discovery）、`app/tools`（业务工具）、`app/agents`（assistant ReAct + 三条能力链）、`app/api`（internal + open_agent）。

## 配置（全部 env 注入，密钥不入仓）

| 变量 | 说明 | 缺省 |
|---|---|---|
| SERVICE_NAME / AI_PORT | 服务名 / 端口 | star-site-platform-ai / 8091 |
| NACOS_SERVER_ADDR / NACOS_REGISTER_IP | 注册中心 / 注册 IP（compose 里填容器名 star-ai） | 127.0.0.1:8848 / 127.0.0.1 |
| AI_LLM_BASE_URL / AI_LLM_API_KEY | GLM OpenAI 兼容端点 / 密钥 | 智谱官方 / 空（空=沙箱） |
| AI_LLM_MODEL / AI_LLM_VISION_MODEL | 文本 / 视觉模型 | glm-4.5-air / glm-4v-flash |
| AI_MOCK_ENABLED | 显式沙箱开关 | 未配（无 key 自动沙箱） |

## 本地运行

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8091   # 无 key 默认沙箱模式
curl localhost:8091/health
```

## Java 接入方式

消费方在自己 `*-integration` 模块定义 `@FeignClient(name = "star-site-platform-ai", path = "/internal/ai")` 契约 + `AiIntegration` 门卫（RpcCallGuard fail-closed）——跨语言服务不建 facade 模块（见 docs/技术/12）。网关已路由 `/api/ai/**`（C 端智能体预留）。
