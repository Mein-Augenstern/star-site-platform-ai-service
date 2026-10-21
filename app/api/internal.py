"""服务间内部能力端点（/internal/ai/**，无 JWT，仅内网；R 信封响应）。"""

from fastapi import APIRouter

from app.agents import chat_graph, finance_diagnosis, report_interpret, report_ocr
from app.config import settings
from app.r import BAD_REQUEST, INTERNAL_ERROR, r_fail, r_ok
from app.schemas import ChatRequest, DiagnosisRequest, InterpretRequest, OcrRequest

router = APIRouter(prefix="/internal/ai")


@router.get("/health")
async def health() -> dict:
    """存活探针（沙箱状态一并暴露，便于排障）。"""
    return {"status": "UP", "service": settings.service_name, "mock": settings.effective_mock}


@router.post("/report-ocr")
async def report_ocr_endpoint(request: OcrRequest) -> dict:
    """拍照 OCR：图片 data URL → 结构化草稿（强类型）。"""
    try:
        return r_ok((await report_ocr.run(request)).model_dump())
    except ValueError as exc:
        return r_fail(BAD_REQUEST, str(exc), retryable=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[report-ocr] 调用失败: {exc}")
        return r_fail(INTERNAL_ERROR, "AI 识别暂不可用，请稍后再试", retryable=True)


@router.post("/report-interpret")
async def report_interpret_endpoint(request: InterpretRequest) -> dict:
    """报告解读：结构化指标 → 白话解读文本。"""
    try:
        return r_ok((await report_interpret.run(request)).model_dump())
    except Exception as exc:  # noqa: BLE001
        print(f"[report-interpret] 调用失败: {exc}")
        return r_fail(INTERNAL_ERROR, "AI 解读暂不可用，请稍后再试", retryable=True)


@router.post("/finance-diagnosis")
async def finance_diagnosis_endpoint(request: DiagnosisRequest) -> dict:
    """财务诊断：三张表 + 收支快照 → 诊断建议文本。"""
    try:
        return r_ok((await finance_diagnosis.run(request)).model_dump())
    except Exception as exc:  # noqa: BLE001
        print(f"[finance-diagnosis] 调用失败: {exc}")
        return r_fail(INTERNAL_ERROR, "AI 诊断暂不可用，请稍后再试", retryable=True)


@router.post("/chat")
async def chat_endpoint(request: ChatRequest) -> dict:
    """通用对话（LangGraph 状态图；无用户身份，供服务间简单调用）。"""
    try:
        return r_ok((await chat_graph.run(request)).model_dump())
    except Exception as exc:  # noqa: BLE001
        print(f"[chat] 调用失败: {exc}")
        return r_fail(INTERNAL_ERROR, "对话服务暂不可用，请稍后再试", retryable=True)
