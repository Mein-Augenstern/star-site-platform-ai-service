"""通用对话智能体（LangGraph StateGraph 底座）：消息状态 + 模型节点。

这是平台智能体的标准形态起点：状态图（StateGraph）+ 条件边可平滑长出
工具调用、多轮记忆、多智能体协作——当前实现「历史消息 + 单模型回复」的最小闭环。
"""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from app.config import settings
from app.llm import chat_model
from app.schemas import ChatRequest, ChatResult

SYSTEM_PROMPT = "你是 star-site 家庭操作台的智能助手，回答简明、务实、说人话；不确定就说不确定。"

MOCK_REPLY = "【沙箱模拟】收到你的消息。当前为沙箱模式（未配置 LLM key），接入真实模型后我将给出完整回答。"


def _build_graph():
    """单节点对话图：START → model → END（历史消息经 MessagesState 自动携带）。"""

    async def call_model(state: MessagesState) -> dict:
        reply = await chat_model().ainvoke([("system", SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [reply]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_edge(START, "model")
    graph.add_edge("model", END)
    return graph.compile()


graph = _build_graph()


async def run(request: ChatRequest) -> ChatResult:
    """执行对话：沙箱模式返回固定模拟回复；真实模式走 LangGraph 图。"""
    if settings.effective_mock:
        return ChatResult(reply=MOCK_REPLY)
    history = [HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content)
               for m in request.history]
    state = {"messages": [*history, HumanMessage(content=request.message)]}
    result = await graph.ainvoke(state)
    reply = result["messages"][-1].content
    return ChatResult(reply=reply)
