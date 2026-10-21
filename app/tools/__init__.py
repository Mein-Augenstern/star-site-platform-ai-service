"""
智能体业务工具集（LangChain @tool）。

LangChain 工具（初学者必读）：

智能体（Agent）的核心能力是「调用工具」——模型根据用户的自然语言输入，
决定要不要调工具、调哪个工具、传什么参数，然后把工具返回的数据融入回答。
这就是 ReAct 模式：Reason（推理该做什么）→ Act（执行工具）→ 观察结果 → 继续推理。

LangChain 的 ``@tool`` 装饰器把一个普通 Python 函数变成智能体可调用的工具：
- 函数名 → 工具名（模型看到的名字）
- 函数 docstring → 工具描述（模型据此判断什么时候该用这个工具）
- 函数参数 → 工具入参（模型负责填入正确的值）
- 返回值 → 工具输出（字符串，会作为上下文传回给模型）

因此：**docstring 就是给模型看的说明书**——写清楚"什么时候用我、需要什么参数"，
模型的工具调用准确率直接取决于 docstring 的质量。

身份安全：
所有工具以「当前对话用户」（memberId）身份收口查询——memberId 存于
``ContextVar``（请求入口由 JWT 验签写入），工具内部读取。
模型（LLM）无法伪造这个值，因为它不在 prompt 里，而是在代码层面注入的。
"""

# ContextVar：Python 的上下文变量——在异步代码中安全传递「请求级」数据
# （每个 HTTP 请求有独立的上下文，互不干扰；工具函数可以从上下文中读取当前用户 ID）
from contextvars import ContextVar
from typing import Optional

# LangChain 工具装饰器
from langchain_core.tools import tool

# 服务发现 + 内网调用
from app.core.discovery import internal_get

# ============================================================
# 用户身份上下文
# ============================================================
# ContextVar 是 Python 异步编程中传递「请求级数据」的标准方式：
# - 每个 HTTP 请求有独立的上下文，A 请求和 B 请求的 member_id 互不干扰
# - set() 写入 → 同一异步任务链内的任何函数都能 get() 读取 → reset() 恢复
# - 相比全局变量：并发安全；相比函数参数：不需要层层传递
member_ctx: ContextVar[int] = ContextVar("member_id", default=0)


def current_member_id() -> int:
    """获取当前对话用户的 ID。

    Returns:
        int: 用户 ID（对应数据库 member 表的 id）。

    Raises:
        ValueError: 上下文中没有用户 ID（请求入口忘记写入），拒绝执行查询。
    """
    member_id = member_ctx.get()

    # fail-closed：没有身份就不查数据（防止越权查询别人的数据）
    if not member_id:
        raise ValueError("缺少用户身份（memberId），拒绝执行查询")

    return member_id


# ============================================================
# 工具定义
# ============================================================

@tool
def list_family_members() -> str:
    """列出当前用户家庭的全部成员档案（姓名/关系/生日）。当用户提到"我爸妈""家人""妈妈最近"等需要确定查询对象时先用本工具。"""
    member_id = current_member_id()

    # 经服务发现调 family 服务的内部端点（无 JWT，靠内网隔离 + memberId 收口归属）
    r = _run(internal_get("star-site-biz-family", "/internal/family/members",
                          {"memberId": member_id}))

    # R 信封检查：code != 0 = 业务失败
    if r.get("code") != 0:
        return f"查询家庭成员失败：{r.get('message')}"

    members = r.get("data") or []

    # 空列表提示
    if not members:
        return "家庭还没有成员档案。"

    # 逐行格式化为工具输出文本（LLM 可读的纯文本）
    return "\n".join(
        f"- id={m['id']} 姓名={m['name']} 关系={m.get('relation')} 生日={m.get('birthDate') or '未填'}"
        for m in members)


@tool
def list_recent_reports(family_member_id: Optional[int] = None) -> str:
    """列出体检报告摘要（最近 20 份，不含指标明细）。family_member_id 可选——指定成员则单查该成员；不传查全家。先用 list_family_members 拿到成员 id。"""
    member_id = current_member_id()

    # 构造查询参数（family_member_id 可选）
    params: dict = {"memberId": member_id}
    if family_member_id:
        params["familyMemberId"] = family_member_id

    # 调 health 服务的报告列表内部端点
    r = _run(internal_get("star-site-biz-health", "/internal/health/reports", params))

    # R 信封检查：code != 0 = 业务失败
    if r.get("code") != 0:
        return f"查询报告列表失败：{r.get('message')}"

    reports = r.get("data") or []

    # 空列表提示
    if not reports:
        return "还没有体检报告。"

    # 格式化为工具输出文本
    return "\n".join(
        f"- report_id={r['id']} 《{r['title']}》 成员id={r['familyMemberId']} "
        f"{r.get('checkupDate') or str(r.get('reportYear')) + '年'}"
        f"{(' · ' + r['hospital']) if r.get('hospital') else ''}"
        for r in reports)


@tool
def get_report_detail(report_id: int) -> str:
    """查一份体检报告的完整指标明细（含每项数值/参考区间/偏高偏低标记）。report_id 从 list_recent_reports 获得。"""
    member_id = current_member_id()

    # 调 health 服务的报告详情内部端点
    r = _run(internal_get("star-site-biz-health",
                          f"/internal/health/reports/{report_id}",
                          {"memberId": member_id}))

    # R 信封检查：code != 0 = 业务失败
    if r.get("code") != 0:
        return f"查询报告失败：{r.get('message')}"

    report = r.get("data") or {}

    # 报告头部信息
    lines = [f"《{report.get('title')}》 成员id={report.get('familyMemberId')} "
             f"{report.get('checkupDate') or str(report.get('reportYear')) + '年'}"
             f"{(' · ' + report['hospital']) if report.get('hospital') else ''}"]

    # 逐项格式化指标（数值/文本 + 参考区间 + 异常标记）
    for i in report.get("indicators") or []:
        # 数值优先，无数值时用文本（如 定性指标"±"）
        value = i.get("valueNum") if i.get("valueNum") is not None else (i.get("valueText") or "-")
        # 异常标志映射：1=偏高, 2=偏低（与 Java 侧 health_indicator 表的 abnormal_flag 一致）
        flag = {1: "【偏高】", 2: "【偏低】"}.get(i.get("abnormalFlag") or 0, "")
        # 参考区间（有值才显示）
        ref = (f"（参考 {i.get('refLow') or '-'}~{i.get('refHigh') or '-'}）"
               if (i.get("refLow") or i.get("refHigh")) else "")
        lines.append(f"- {i.get('itemName')}: {value}{i.get('unit') or ''}{ref} {flag}")

    return "\n".join(lines)


@tool
def get_balance_sheet() -> str:
    """查当前用户的家庭资产负债表（总资产/总负债/净资产/档案资产）。当用户问"我们家资产/负债/净资产"时用本工具。"""
    member_id = current_member_id()

    # 调 finance 服务的资产负债表内部端点
    r = _run(internal_get("star-site-biz-finance", "/internal/finance/balance-sheet",
                          {"memberId": member_id}))

    # R 信封检查：code != 0 = 业务失败
    if r.get("code") != 0:
        return f"查询资产负债表失败：{r.get('message')}"

    sheet = r.get("data") or {}

    # 档案资产可用性（archive 服务可能不可用）
    archive = (sheet.get("archiveAsset")
               if sheet.get("archiveAvailable")
               else "未知（档案服务不可用）")

    # 格式化输出
    return (f"总资产 {sheet.get('totalAsset')} 元（其中档案资产 {archive}），"
            f"总负债 {sheet.get('totalLiability')} 元，净资产 {sheet.get('netWorth')} 元。")


def _run(coro):
    """在同步工具函数中运行异步协程。

    LangChain 的 @tool 装饰器默认生成同步工具——但我们的 HTTP 调用是异步的
    （httpx.AsyncClient）。asyncio.run() 会创建一个临时事件循环来执行协程，
    执行完毕后关闭循环并返回结果。

    为什么不用 await：因为 @tool 标注的函数是同步的（LangChain 工具协议），
    不能直接 await。如果需要真正的异步工具，应该用 @tool 装饰 async 函数。

    Args:
        coro: 要执行的协程对象（如 ``internal_get(...)`` 返回的协程）。

    Returns:
        协程的执行结果。
    """
    import asyncio
    return asyncio.run(coro)


# 导出工具列表（供智能体的 create_react_agent 使用）
TOOLS = [list_family_members, list_recent_reports, get_report_detail, get_balance_sheet]
