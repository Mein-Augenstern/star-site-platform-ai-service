"""报告解读能力（LCEL 链）：结构化指标 → 白话解读文本。

prompt 从 Java ReportInterpretationService 原样迁入（角色约束四条 + 300 字上限）。
"""

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from app.config import settings
from app.llm import chat_model
from app.schemas import InterpretRequest, InterpretResult

SYSTEM_PROMPT = """你是家庭健康助手。请用通俗易懂的中文解读这份体检报告：
1. 先总体评价健康状况；
2. 逐条指出异常指标（偏高/偏低）及其常见含义；
3. 给出饮食、运动、复查方面的建议；
4. 最后必须声明：以上分析仅供参考，不构成医疗诊断，异常指标请遵医嘱就诊。
全文 300 字以内，不要使用 Markdown 标记。"""

MOCK_TEXT = "【沙箱模拟】总体健康状况平稳。检出 1 项偏高指标（模拟数据）：建议低盐低脂饮食、规律作息，" \
            "3 个月后复查对照。以上分析仅供参考，不构成医疗诊断，异常指标请遵医嘱就诊。"


def _render_indicators(request: InterpretRequest) -> str:
    """把结构化指标渲染成模型易读的行式清单（与 Java 原版拼接格式一致）。"""
    lines = []
    for i in request.indicators:
        line = f"- {i.name}: {i.value}"
        if i.unit:
            line += i.unit
        if i.refLow or i.refHigh:
            line += f"（参考 {i.refLow or '-'}~{i.refHigh or '-'}）"
        if i.abnormalFlag == 1:
            line += "【偏高】"
        elif i.abnormalFlag == 2:
            line += "【偏低】"
        lines.append(line)
    return "\n".join(lines)


async def run(request: InterpretRequest) -> InterpretResult:
    """执行解读：沙箱模式返回模拟文本；真实模式走 LCEL 链。"""
    if settings.effective_mock:
        return InterpretResult(text=MOCK_TEXT)
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "报告：{report_desc}，共 {count} 项指标：\n{indicators}"),
    ])
    hospital_desc = f"（{request.hospital}）" if request.hospital and request.hospital.strip() else ""
    chain = prompt | chat_model() | StrOutputParser()
    text = await chain.ainvoke({
        "report_desc": f"{request.reportYear}年体检{hospital_desc}",
        "count": len(request.indicators),
        "indicators": _render_indicators(request),
    })
    return InterpretResult(text=text.strip())
