"""拍照 OCR 能力（LCEL 链）：多模态消息 → 视觉模型 → 宽容 JSON 解析 → 强类型 OcrDraft。

prompt 从 Java ReportOcrService 原样迁入——AI 资产（prompt/模型/解析）集中在本服务。
"""

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.config import settings
from app.llm import chat_model
from app.schemas import OcrDraft, OcrIndicator, OcrRequest

# 角色约束（Java 原版迁入）：只输出 JSON、看不清用 null、数值去单位
SYSTEM_PROMPT = """你是体检报告结构化助手。从图片中提取体检报告信息，只输出一个 JSON 对象，不要任何其他文字或 Markdown 代码块标记。
格式：{"title":"标题","hospital":"医院","checkupDate":"YYYY-MM-DD 或 null","reportYear":2026,"indicators":[{"itemName":"指标名","itemGroup":"分组","valueNum":数值或null,"valueText":"文本结果或null","unit":"单位","refLow":下限或null,"refHigh":上限或null}]}
注意：只提取图片中真实存在的内容，看不清的字段用 null；数值去掉单位和逗号。"""

USER_PROMPT = "请提取这份体检报告的结构化信息。"

# 沙箱模拟草稿（明确标记，避免误导真实数据）
MOCK_DRAFT = OcrDraft(
    title="【沙箱模拟】示例体检报告",
    hospital="沙箱医院",
    checkupDate=None,
    reportYear=2026,
    indicators=[
        OcrIndicator(itemName="收缩压", itemGroup="血压", valueNum=138, unit="mmHg", refLow=90, refHigh=140),
        OcrIndicator(itemName="空腹血糖", itemGroup="血糖", valueNum=5.6, unit="mmol/L", refLow=3.9, refHigh=6.1),
    ],
)


def _messages(request: OcrRequest) -> list:
    """多模态消息：图片 data URL + 提取指令。"""
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=[
            {"type": "image_url", "image_url": {"url": request.imageDataUrl}},
            {"type": "text", "text": USER_PROMPT},
        ]),
    ]


async def run(request: OcrRequest) -> OcrDraft:
    """执行 OCR：沙箱模式直接返回模拟草稿；真实模式走视觉模型 + 宽容解析。"""
    if settings.effective_mock:
        return MOCK_DRAFT
    chain = ChatPromptTemplate.from_messages(_messages(request)) | chat_model(vision=True) | StrOutputParser()
    raw = await chain.ainvoke({})
    return _parse(raw)


def _parse(raw: str) -> OcrDraft:
    """宽容解析：剥 Markdown 代码围栏后 json.loads；字段缺失靠 pydantic 默认值兜底。"""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型输出不是合法 JSON: {text[:120]}") from exc
    indicators = [OcrIndicator(**item) for item in data.get("indicators") or []]
    return OcrDraft(
        title=data.get("title"),
        hospital=data.get("hospital"),
        checkupDate=data.get("checkupDate"),
        reportYear=data.get("reportYear"),
        indicators=indicators,
    )
