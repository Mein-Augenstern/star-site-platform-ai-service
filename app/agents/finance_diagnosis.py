"""财务健康诊断能力（LCEL 链）：三张表 + 近月收支快照 → 诊断建议文本。

prompt 从 Java FinanceDiagnosisService 原样迁入（务实温和、不推销、350 字上限）。
"""

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from app.config import settings
from app.llm import chat_model
from app.schemas import DiagnosisRequest, DiagnosisResult

SYSTEM_PROMPT = """你是家庭财务顾问。请基于给出的家庭财务数据做健康诊断：
1. 用三句话概括资产/收支/现金流的现状；
2. 指出 2-3 个最值得注意的问题或风险（如月供占比过高、应急资金不足、结余率低）；
3. 给出可执行的改善建议；
4. 语气务实温和，不推销任何金融产品，最后声明仅供参考。
全文 350 字以内，不要使用 Markdown 标记。"""

MOCK_TEXT = "【沙箱模拟】家庭资产结构基本稳定，收支为正结余，流动资金覆盖良好。" \
            "值得关注：应急资金建议覆盖 3-6 个月支出；负债占比若升高将挤压结余率。" \
            "建议按月定投留存结余、定期复核保单与负债成本。以上仅供参考，不构成投资建议。"


def _render(request: DiagnosisRequest) -> str:
    """把结构化快照渲染成模型易读的数据清单（与 Java 原版拼接格式一致）。"""
    sheet = request.sheet
    archive = sheet.archiveAsset if sheet.archiveAvailable else "未知"
    accounts = " ".join(f"{a.name}({a.balance})" for a in request.accounts)
    months = request.cash.monthsCovered or "充裕"
    return (
        f"- 总资产：{sheet.totalAsset}（其中档案资产估值 {archive}）\n"
        f"- 总负债：{sheet.totalLiability}\n"
        f"- 净资产：{sheet.netWorth}\n"
        f"- 上月收入：{request.lastMonth.income}，支出：{request.lastMonth.expense}，结余：{request.lastMonth.surplus}\n"
        f"- 流动资金：{request.cash.liquidBalance}，可撑月数：{months}\n"
        f"- 账户清单：{accounts}\n"
    )


async def run(request: DiagnosisRequest) -> DiagnosisResult:
    """执行诊断：沙箱模式返回模拟文本；真实模式走 LCEL 链。"""
    if settings.effective_mock:
        return DiagnosisResult(text=MOCK_TEXT)
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "家庭财务数据：\n{data}"),
    ])
    chain = prompt | chat_model() | StrOutputParser()
    text = await chain.ainvoke({"data": _render(request)})
    return DiagnosisResult(text=text.strip())
