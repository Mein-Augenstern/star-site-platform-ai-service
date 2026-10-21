"""家庭助手智能体（大厂标准形态：LangGraph prebuilt ReAct + 工具调用 + 线程级记忆）。

- 真实模式：create_react_agent（模型绑定工具 → 决策调工具 → 归纳回答，循环至答案），
  MemorySaver 按 thread_id 存对话状态（生产换 SqliteSaver/PostgresSaver 仅改一行）；
- 沙箱模式（无 key / AI_MOCK_ENABLED=true）：降级为 FakeListChatModel 的简化图——
  ReAct 需要真模型的 bind_tools 能力，沙箱不调模型不花钱，输出带【沙箱模拟】标记；
- 用户身份经 MemberContext（ContextVar）注入工具，模型无法伪造身份；
- 流式：astream_events 逐 token 下发（SSE）。
"""

import itertools

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from app.config import settings
from app.llm import chat_model
from app.tools import TOOLS

SYSTEM_PROMPT = """你是 star-site 家庭操作台的智能助手「小星」，帮助用户打理全家健康与财务。
你有一组工具可以查询家庭成员、体检报告与指标明细、家庭资产负债表——
回答涉及具体数据的问题时必须调用工具查真实数据，禁止编造数值；
先理解用户提到的是哪位家人（用 list_family_members 对照），再查对应数据；
回答用简明中文，先给结论再给依据；健康话题最后提醒：仅供参考，遵医嘱就诊。"""


def _build_react():
    """真实模式：ReAct 工具调用智能体（langgraph 0.2.x 的系统提示参数是 state_modifier）。"""
    return create_react_agent(
        chat_model(),
        tools=TOOLS,
        state_modifier=SYSTEM_PROMPT,
        checkpointer=MemorySaver(),
    )


def _build_mock():
    """沙箱模式：FakeListChatModel 简化图（循环返回固定 AIMessage，不调工具不花钱）。"""
    model = FakeMessagesListChatModel(
        responses=itertools.cycle([AIMessage(content="【沙箱模拟】收到。当前为沙箱模式，"
                                                   "接入真实模型后我将调用工具查询你的家庭数据。")]))
    graph = StateGraphStub(model)
    return graph


class StateGraphStub:
    """沙箱简化图：与 ReAct 图同接口（ainvoke/astream_events），行为为单模型节点。"""

    def __init__(self, model):
        self._model = model
        self.checkpointer = MemorySaver()

    async def _node(self, state, config=None):
        reply = await self._model.ainvoke([("system", SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [reply]}

    def ainvoke(self, state, config=None):
        return self._run(state)

    def astream_events(self, state, config=None, version="v2"):
        return self._stream(state)

    async def _run(self, state):
        reply = await self._node(state)
        return {"messages": state["messages"] + reply["messages"]}

    async def _stream(self, state):
        reply = await self._node(state)
        content = reply["messages"][-1].content
        yield ("token", content)


graph = _build_mock() if settings.effective_mock else _build_react()


async def run(message: str, conversation_id: str, member_id: int) -> str:
    """非流式执行：返回最终回答（多轮记忆按 conversation_id 隔离）。"""
    from app.tools import member_ctx
    state = {"messages": [("user", message)]}
    config = {"configurable": {"thread_id": conversation_id}}
    token = member_ctx.set(member_id)
    try:
        result = await graph.ainvoke(state, config)
    finally:
        member_ctx.reset(token)
    return result["messages"][-1].content


async def stream(message: str, conversation_id: str, member_id: int):
    """流式执行：逐事件产出模型 token（SSE 用；工具调用事件以状态行下发）。"""
    from app.tools import member_ctx
    state = {"messages": [("user", message)]}
    config = {"configurable": {"thread_id": conversation_id}}
    token = member_ctx.set(member_id)
    try:
        async for event in graph.astream_events(state, config, version="v2"):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                piece = event["data"]["chunk"].content
                if piece:
                    yield ("token", piece)
            elif kind == "on_tool_start":
                yield ("status", f"正在调用工具 {event['name']} …")
    finally:
        member_ctx.reset(token)
