"""
能力端点的请求/响应模型（Pydantic v2）。

Pydantic 是 Python 生态最流行的数据验证库——
模型类继承 ``BaseModel``，每个字段用 ``Field`` 声明类型、默认值和描述：

.. code-block:: python

    class Person(BaseModel):
        name: str = Field(description="姓名")          # 必填字段
        age: int = Field(default=0, description="年龄") # 可选字段（有默认值）

当 FastAPI 收到 HTTP 请求时，自动将 JSON body 反序列化为 Pydantic 模型实例，
并按 Field 的类型和约束做验证——类型不匹配/缺必填字段会自动返回 422 错误。
开发者不需要手写参数校验代码，Pydantic 帮你做了。

命名风格说明：
字段名使用 camelCase（如 ``imageDataUrl``）而不是 snake_case，
是为了与 Java 侧的 DTO 字段名直接对齐（Java DTO 用 camelCase），
避免 JSON 序列化/反序列化时的字段名映射。
"""

# typing：Python 类型标注的标准库
from typing import List, Optional

# pydantic：数据验证 + JSON 序列化/反序列化
from pydantic import BaseModel, Field


# ============================================================
# ---- 拍照 OCR（报告图片 → 结构化草稿）----
# ============================================================

class OcrRequest(BaseModel):
    """拍照识别请求（POST /internal/ai/report-ocr 的入参）。"""

    # 报告图片的 data URL 格式：data:image/jpeg;base64,/9j/4AAQ...
    # 前缀 "data:image/xxx;base64," 告诉模型这是哪种编码格式的图片
    imageDataUrl: str = Field(description="报告图片 data URL（data:image/xxx;base64,...）")


class OcrIndicator(BaseModel):
    """识别出的单项指标草稿（对应数据库 health_indicator 表的一行）。

    值的两种形态：
    - valueNum：数值型指标（如 收缩压 148.0）——可以画趋势图、算参考区间；
    - valueText：文本型指标（如 尿蛋白 ±）——只能展示，不能画图。
    二者二选一有值，不会同时为空。
    """

    # 指标名（如 空腹血糖）——必填，其他字段都可以为空
    itemName: str = Field(description="指标名（如 空腹血糖）")
    # 指标所属分组（如 血脂四项、血常规）——用于报告详情页按组展示
    itemGroup: Optional[str] = Field(default=None, description="指标分组（如 血脂四项）")
    # 数值型结果——可以画趋势图、和参考区间做数值比较
    valueNum: Optional[float] = Field(default=None, description="数值结果（与 valueText 二选一）")
    # 文本型结果——定性指标（如 尿蛋白 ±、HBsAg 阴性）没有数值只有文字
    valueText: Optional[str] = Field(default=None, description="文本结果（定性指标如 尿蛋白±）")
    # 计量单位（如 mmol/L、mmHg、g/L）
    unit: Optional[str] = Field(default=None, description="单位")
    # 参考区间下限——低于此值可能偏低（abnormalFlag=2）
    refLow: Optional[float] = Field(default=None, description="参考区间下限")
    # 参考区间上限——高于此值可能偏高（abnormalFlag=1）
    refHigh: Optional[float] = Field(default=None, description="参考区间上限")


class OcrDraft(BaseModel):
    """OCR 结构化草稿（强类型出参）。

    「草稿」的含义：AI 识别的结果只是预填表单的草稿——
    用户在前端核对、修正后才保存到数据库。
    AI 识别可能出错（尤其是手写体和模糊图片），所以必须人工确认。
    """

    # 报告标题（识别不出时为 None，前端展示"未命名报告"）
    title: Optional[str] = Field(default=None, description="报告标题")
    # 医院名称
    hospital: Optional[str] = Field(default=None, description="医院")
    # 体检日期（YYYY-MM-DD 格式字符串；识别不出时为 None，前端让用户手填）
    checkupDate: Optional[str] = Field(default=None, description="体检日期（YYYY-MM-DD 或 null）")
    # 报告年份（从 checkupDate 提取，或直接识别年份）
    reportYear: Optional[int] = Field(default=None, description="报告年份")
    # 识别出的指标草稿列表（每项对应 health_indicator 表的一行）
    indicators: List[OcrIndicator] = Field(default_factory=list, description="指标草稿列表")


# ============================================================
# ---- 报告解读（结构化指标 → 白话文本）----
# ============================================================

class InterpretIndicator(BaseModel):
    """解读入参的指标行。

    注意：abnormalFlag 由 Java 侧（health 服务）根据参考区间预先判定，
    AI 只需要解读"偏高/偏低"的临床含义，不需要自己判断是否异常。
    """

    # 指标名（如 空腹血糖、低密度脂蛋白）
    name: str = Field(description="指标名")
    # 检测结果（数值或文本，如 "148" 或 "±"）
    value: str = Field(description="结果（数值或文本）")
    # 计量单位（如 mmol/L）
    unit: Optional[str] = Field(default=None, description="单位")
    # 参考区间下限（字符串格式，直接透传给模型）
    refLow: Optional[str] = Field(default=None, description="参考下限")
    # 参考区间上限
    refHigh: Optional[str] = Field(default=None, description="参考上限")
    # 异常标志：0=正常, 1=偏高, 2=偏低（由 Java 侧根据参考区间判定）
    abnormalFlag: int = Field(default=0, description="0正常 1偏高 2偏低")


class InterpretRequest(BaseModel):
    """报告解读请求（结构化数据，prompt 模板在本服务中）。

    Java 侧（health 服务）把报告和指标聚合成此结构，
    POST 到本服务的 /internal/ai/report-interpret 端点。
    """

    # 报告年份（如 2026）
    reportYear: int = Field(description="报告年份")
    # 医院名称（可空——用户可能没填）
    hospital: Optional[str] = Field(default=None, description="医院")
    # 指标全集（该报告下的所有体检指标）
    indicators: List[InterpretIndicator] = Field(default_factory=list, description="指标全集")


class InterpretResult(BaseModel):
    """解读结果（模型生成的白话中文文本）。"""

    # AI 生成的解读文本（300 字以内的白话中文）
    text: str = Field(description="解读文本（白话中文）")


# ============================================================
# ---- 财务诊断（三张表 + 收支快照 → 诊断建议）----
# ============================================================

class AccountBrief(BaseModel):
    """账户简要信息（诊断展示用，只取名称和余额）。"""

    # 账户名（如 工资卡、微信零钱）
    name: str = Field(description="账户名")
    # 当前余额（字符串格式，如 "105000.00"，直接透传给模型）
    balance: str = Field(description="余额（元）")


class SheetSummary(BaseModel):
    """资产负债表摘要（对应 Java 侧 BalanceSheetVO 的关键字段）。"""

    # 总资产 = 账户资产 + 档案资产（元）
    totalAsset: str = Field(description="总资产（元）")
    # 档案资产估值（archive 服务管理的实物资产；未知时传字符串"未知"）
    archiveAsset: str = Field(description="档案资产估值（元；未知时为 未知）")
    # archive 服务是否可用（不可用时档案资产按 0 计入）
    archiveAvailable: bool = Field(default=False, description="archive 服务是否可用")
    # 总负债 = 所有负债账户的欠款合计（元）
    totalLiability: str = Field(description="总负债（元）")
    # 净资产 = 总资产 - 总负债（元）
    netWorth: str = Field(description="净资产（元）")


class MonthSummary(BaseModel):
    """单月收支摘要（上月收入-支出-结余）。"""

    # 上月收入合计（元）
    income: str = Field(description="收入（元）")
    # 上月支出合计（元）
    expense: str = Field(description="支出（元）")
    # 上月结余 = 收入 - 支出（元）
    surplus: str = Field(description="结余（元）")


class CashSummary(BaseModel):
    """现金流摘要（流动资金能覆盖几个月的基本支出）。"""

    # 流动资金（现金 + 活期 + 货币基金等可随时动用的钱，元）
    liquidBalance: str = Field(description="流动资金（元）")
    # 可撑月数 = 流动资金 / 月均支出（充裕时传字符串"充裕"）
    monthsCovered: str = Field(description="可撑月数（充裕时为 充裕）")


class DiagnosisRequest(BaseModel):
    """财务健康诊断请求（三张表 + 近月收支快照）。

    Java 侧（finance 服务）把资产负债表、收支表、现金流聚合为此结构，
    POST 到 AI 服务的诊断端点。
    """

    # 资产负债摘要
    sheet: SheetSummary = Field(description="资产负债摘要")
    # 上月收支摘要
    lastMonth: MonthSummary = Field(description="上月收支摘要")
    # 现金流摘要
    cash: CashSummary = Field(description="现金流摘要")
    # 账户清单（每个账户的名称和余额，帮助模型理解资产分布）
    accounts: List[AccountBrief] = Field(default_factory=list, description="账户清单")


class DiagnosisResult(BaseModel):
    """诊断结果（模型生成的建议文本）。"""

    # AI 生成的诊断建议文本（350 字以内的白话中文）
    text: str = Field(description="诊断建议文本（白话中文）")


# ============================================================
# ---- 通用对话（智能体底座）----
# ============================================================

class ChatMessage(BaseModel):
    """单条历史消息（多轮对话的记忆单元）。"""

    # 消息角色："user" = 用户说的，"assistant" = AI 回复的
    role: str = Field(description="user/assistant")
    # 消息文本内容
    content: str = Field(description="消息内容")


class ChatRequest(BaseModel):
    """对话请求（由 LangGraph 状态图驱动）。"""

    # 用户本轮输入的消息
    message: str = Field(description="用户消息")
    # 历史消息列表（多轮对话时传入，让模型记住上下文；单轮可不传）
    history: List[ChatMessage] = Field(default_factory=list, description="历史消息（可选，多轮记忆）")


class ChatResult(BaseModel):
    """对话结果。"""

    # AI 生成的回复文本
    reply: str = Field(description="助手回复")
