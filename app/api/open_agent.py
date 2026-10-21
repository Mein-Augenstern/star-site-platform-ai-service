"""C 端智能体端点（/api/ai/agent/**，JWT 鉴权）。

身份链路：网关验签放行 → 本服务二次验签拿 memberId（sub）→ 写入 MemberContext
→ ReAct 工具以该身份查询真实家庭数据（模型无法伪造身份）。
ask = 一次性 JSON 回答；chat = SSE 流式（token 逐个下发）。
"""

import json

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents import assistant
from app.core.security import InvalidToken, parse_member_id
from app.r import BAD_REQUEST, r_fail, r_ok

router = APIRouter(prefix="/api/ai/agent")


class AgentChatRequest(BaseModel):
    """智能体对话请求。"""

    message: str = Field(description="用户消息")
    conversation_id: str | None = Field(default=None, description="会话 id（多轮记忆隔离；缺省按匿名单轮）")


def _member_or_none(authorization: str | None) -> int | None:
    """从 Authorization: Bearer 头解析 memberId；解析失败返回 None（走未登录兜底）。"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        return parse_member_id(authorization[len("Bearer "):].strip())
    except InvalidToken:
        return None


@router.post("/ask")
async def ask(request: AgentChatRequest, authorization: str | None = Header(default=None)) -> dict:
    """非流式：智能体带工具查询真实数据后给出最终回答。"""
    member_id = _member_or_none(authorization)
    if not member_id:
        return r_fail(BAD_REQUEST, "需要登录后使用智能体", retryable=False)
    message = request.message.strip()
    if not message:
        return r_fail(BAD_REQUEST, "消息不能为空", retryable=False)
    conversation_id = request.conversation_id or f"anon-{member_id}"
    try:
        reply = await assistant.run(message, conversation_id, member_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[agent/ask] 执行失败: {exc}")
        return r_fail(500, "智能体暂不可用，请稍后再试", retryable=True)
    return r_ok({"reply": reply})


@router.post("/chat")
async def chat(request: AgentChatRequest, authorization: str | None = Header(default=None)) -> StreamingResponse:
    """SSE 流式：事件类型 status（工具调用进度）/ token（模型增量）/ done。"""
    member_id = _member_or_none(authorization)
    if not member_id:
        return StreamingResponse(iter(["data: {\"error\": \"需要登录后使用智能体\"}\n\n"]),
                                 media_type="text/event-stream", status_code=401)
    message = request.message.strip()
    conversation_id = request.conversation_id or f"anon-{member_id}"

    async def event_source():
        try:
            async for kind, payload in assistant.stream(message, conversation_id, member_id):
                yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: [DONE]\n\n"
        except Exception as exc:  # noqa: BLE001
            print(f"[agent/chat] 流式失败: {exc}")
            yield f"event: error\ndata: {json.dumps('智能体暂不可用，请稍后再试', ensure_ascii=False)}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")
