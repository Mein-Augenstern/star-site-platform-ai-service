"""FastAPI 应用：AI 能力端点（统一平台 R 信封）+ 智能体端点 + Nacos 注册生命周期。

路由分三层：api/internal（服务间能力端点）、api/open_agent（C 端智能体，JWT + SSE）、
/health 存活探针。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import internal, open_agent
from app.config import settings
from app.nacos_register import start_background_register


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动挂 Nacos 注册/心跳，关闭时收尾。"""
    start_background_register()
    yield


app = FastAPI(title="star-site-platform-ai", version="0.2.0", lifespan=lifespan)

# 服务间能力端点（无 JWT，仅内网）
app.include_router(internal.router)
# C 端智能体（JWT 鉴权，ReAct 工具调用 + SSE 流式）
app.include_router(open_agent.router)


@app.get("/health")
async def health() -> dict:
    """存活探针（沙箱状态一并暴露，便于排障）。"""
    return {"status": "UP", "service": settings.service_name, "mock": settings.effective_mock}
