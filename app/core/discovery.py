"""
Nacos 服务发现 + 内网 HTTP 调用助手。

服务发现的概念（初学者必读）：

在微服务架构中，服务之间通过 HTTP 互相调用，但调用方通常不知道目标服务的
IP 和端口（因为容器重启后 IP 会变）。Nacos 作为「电话簿」解决了这个问题：

1. 每个服务启动时向 Nacos 注册：「我叫 star-site-biz-health，地址是 star-health:8084」
2. 调用方要调 health 服务时，先问 Nacos：「star-site-biz-health 的地址是什么？」
3. Nacos 返回所有健康实例的 IP:端口列表
4. 调用方从列表中选一个（负载均衡）发起 HTTP 请求

本模块的 ``resolve()`` 就是第 2-3 步的实现：查 Nacos → 拿实例列表 → 轮询选一个。

缓存策略：查询结果缓存 10 秒——避免每次调用都去查 Nacos（增加 Nacos 压力
+ 增加调用延迟）。10 秒内的实例上下线不会立即感知，但对大多数场景足够。
"""

# asyncio：异步 I/O（httpx 的异步模式需要）
import asyncio
# itertools.count：生成无限递增序列（轮询计数器用）
import itertools
# time.monotonic：单调时钟（不受系统时间回拨影响，适合测量时间间隔）
import time
from typing import Optional

# httpx：支持 async/await 的现代 HTTP 客户端
import httpx

# 导入配置单例（Nacos 地址、命名空间等）
from app.config import settings

# ============================================================
# 服务实例缓存
# ============================================================
# 结构：{服务名: (缓存时间戳, [基址列表])}
# 例：{"star-site-biz-health": (1234567890.0, ["http://star-health:8084"])}
_CACHE: dict[str, tuple[float, list[str]]] = {}

# 缓存有效期（秒）——超过 10 秒重新查询 Nacos
_CACHE_TTL = 10.0

# 轮询计数器（多个实例时轮流使用，实现简单的负载均衡）
_counter = itertools.count()


class DiscoveryError(Exception):
    """目标服务无可用实例（Nacos 中没有健康实例或查询失败）。"""


async def resolve(service: str) -> str:
    """解析服务名 → HTTP 基地址（从 Nacos 获取健康实例列表，轮询选择）。

    「轮询」负载均衡：如果有 3 个实例，第 1 次调用走实例 1，第 2 次走实例 2，
    第 3 次走实例 3，第 4 次又回到实例 1——把流量均匀分配到各个实例上。

    Args:
        service: 目标服务的 Nacos 服务名（如 ``star-site-biz-health``）。

    Returns:
        str: 目标服务的 HTTP 基地址，如 ``http://star-health:8084``。

    Raises:
        DiscoveryError: 目标服务没有健康实例（全部下线或从未注册）。
    """
    # time.monotonic() 返回单调递增的时钟值（不受系统时间修改影响），
    # 适合用来计算时间间隔（判断缓存是否过期）
    now = time.monotonic()

    # 先查缓存（避免每次都去问 Nacos，减少延迟和 Nacos 压力）
    cached = _CACHE.get(service)

    # 缓存有效条件：有缓存 AND 未过期 AND 实例列表非空
    if cached and now - cached[0] < _CACHE_TTL and cached[1]:
        base_list = cached[1]
    else:
        # 缓存过期或无缓存 → 重新查询 Nacos
        base_list = await _query(service)
        # 更新缓存
        _CACHE[service] = (now, base_list)

    # 没有可用实例 → 抛出明确异常（调用方应该给出友好提示）
    if not base_list:
        raise DiscoveryError(f"服务 {service} 无可用实例")

    # 轮询选择：用全局计数器对实例数取模，实现轮流分配
    idx = next(_counter) % len(base_list)
    return base_list[idx]


async def _query(service: str) -> list[str]:
    """向 Nacos 查询指定服务的健康实例列表。

    Args:
        service: Nacos 服务名。

    Returns:
        list[str]: 健康实例的基地址列表（如 ``["http://star-health:8084"]``）。
                   查询失败或无实例时返回空列表。
    """
    # Nacos v1 Open API：查询服务实例列表
    url = f"http://{settings.nacos_server_addr}/nacos/v1/ns/instance/list"

    try:
        # 短连接查询（低频操作，不需要长连接池）
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url, params={
                "serviceName": service,           # 要查的服务名
                "namespaceId": settings.nacos_namespace,  # 命名空间
                "groupName": settings.nacos_group,        # 分组
                "healthyOnly": "true",                    # 只返回健康实例
            })
            # 解析响应 JSON：{"hosts": [{"ip": "...", "port": ..., "healthy": true}, ...]}
            hosts = (resp.json() or {}).get("hosts") or []
    except Exception as exc:
        # 查询失败（Nacos 不可达等）→ 返回空列表（调用方会发现无可用实例）
        print(f"[discovery] 查询 {service} 失败: {exc}")
        return []

    # 拼接基地址：http://IP:PORT（过滤掉不健康的实例）
    return [f"http://{h['ip']}:{h['port']}" for h in hosts if h.get("healthy")]


async def internal_get(
    service: str,
    path: str,
    params: Optional[dict] = None,
) -> dict:
    """调用目标服务的 /internal/** GET 端点，返回平台 R 信封。

    /internal/** 端点不经网关（外网 404），仅限内网服务间调用——
    智能体工具通过本方法以服务间身份查询业务数据（无 JWT，靠内网隔离保证安全）。

    Args:
        service: 目标服务的 Nacos 服务名（如 ``star-site-biz-health``）。
        path:    API 路径（如 ``/internal/health/reports/601``）。
        params:  URL 查询参数（可选）。

    Returns:
        dict: 目标服务返回的平台 R 信封
              ``{"code": 0, "message": "...", "data": {...}, "retryable": ...}``。
              网络失败时返回合成的失败 R（code=503），调用方统一按 code 判断。
    """
    # 第一步：服务发现——解析目标服务的地址
    base = await resolve(service)

    try:
        # 第二步：发 HTTP GET 请求
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(base + path, params=params)
            # 目标服务返回 JSON（平台 R 信封格式），直接解析为 dict
            return resp.json()
    except Exception as exc:
        # 网络失败：返回合成的失败 R（调用方统一按 code != 0 判断）
        # fail-closed：不假装成功，让调用方明确知道依赖不可用了
        print(f"[discovery] GET {service}{path} 失败: {exc}")
        return {
            "code": 503,
            "message": f"{service} 暂不可用",
            "data": None,
            "retryable": True,
        }
