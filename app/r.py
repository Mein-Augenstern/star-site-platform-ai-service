"""
平台统一响应信封（R envelope）—— 所有 API 的出参都用同一个 JSON 结构。

.. code-block:: json

    {
      "code": 0,           // 0 = 成功，非 0 = 失败
      "message": "成功",    // 用户可读的消息
      "data": { ... },     // 业务数据（失败时为 null）
      "retryable": true    // 失败时可重试标记（成功时省略）
    }

为什么需要统一信封（R envelope 模式）：

1. 前端只需要写一套通用的响应拦截器（判断 code 是否为 0），
   不用每个接口单独处理成功/失败格式；
2. 跨服务调用方（Java Feign → 本服务）也用同一结构，
   消费方无差别处理——RpcClientAspect 统一检查 R.code 并自动翻译异常；
3. 失败时附带 ``retryable`` 字段告诉调用方"重试有没有意义"——
   超时/依赖不可用 = 可以重试，参数错误 = 重试也没用。

为什么用 dict 而不是 dataclass：
Python 侧直接返回 dict，FastAPI 序列化成 JSON 时零额外开销。
Java 侧用类（因为要类型安全），Python 用 dict（因为 JSON 天然就是 dict），
两种语言各用最自然的方式表达同一个结构。
"""

from typing import Any, Optional

# 常用错误码（与 Java ResultCode 枚举对齐，全平台统一）
# 0 = 成功；4xx = 客户端错误（参数/权限）；5xx = 服务端错误
SUCCESS = 0
BAD_REQUEST = 400          # 请求参数错误
UNAUTHORIZED = 401         # 未登录或 token 过期
FORBIDDEN = 403            # 无权限访问
NOT_FOUND = 404            # 资源不存在
INTERNAL_ERROR = 500       # 系统内部错误
SERVICE_UNAVAILABLE = 503  # 依赖服务暂不可用


def r_ok(data: Any = None) -> dict:
    """构造成功响应（code=0）。

    成功响应的 ``retryable`` 字段恒为 None（JSON 序列化时被省略），
    因为"成功"不存在"要不要重试"的问题。

    Args:
        data: 业务数据。
              可以是任意可 JSON 序列化的对象（dict / list / str / int / None）。
              接口无返回数据时传 None（或省略）。

    Returns:
        dict: 完整响应信封，
              形如 ``{"code": 0, "message": "成功", "data": ..., "retryable": None}``。

    使用示例::

        >>> r_ok({"name": "钱七"})
        {"code": 0, "message": "成功", "data": {"name": "钱七"}, "retryable": None}

        >>> r_ok()  # 无返回数据的接口
        {"code": 0, "message": "成功", "data": None, "retryable": None}
    """
    return {"code": 0, "message": "成功", "data": data, "retryable": None}


def r_fail(
    code: int,
    message: str,
    retryable: Optional[bool] = None,
) -> dict:
    """构造失败响应（code != 0）。

    Args:
        code:      错误码（400=参数错误, 401=未认证, 403=无权限, 500=系统异常, 503=暂不可用）。
        message:   用户可读的错误描述（会直接展示给前端/调用方）。
        retryable: 可重试标记。
                   - ``True``  = 调用方可重试（依赖超时 / 目标服务未就绪 / HTTP 5xx）
                   - ``False`` = 业务性失败，重试无意义（参数错误 / 无权限）
                   - ``None``  = 未声明（调用方自行判断）

    Returns:
        dict: 完整响应信封（code != 0, data = None）。

    使用示例::

        >>> r_fail(403, "无管理员权限", retryable=False)
        {"code": 403, "message": "无管理员权限", "data": None, "retryable": False}

        >>> r_fail(503, "AI 服务暂不可用", retryable=True)
        {"code": 503, "message": "AI 服务暂不可用", "data": None, "retryable": True}
    """
    return {
        "code": code,
        "message": message,
        "data": None,
        "retryable": retryable,
    }
