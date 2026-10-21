"""
Nacos 服务注册与心跳模块。

微服务「服务发现」概念（初学者必读）：

微服务架构中，服务之间不通过硬编码 IP 调用，而是通过「服务名」路由：

    传统方式（紧耦合）：  http://192.168.1.100:8084/api/health/reports
    服务发现（松耦合）：  http://star-site-biz-health/api/health/reports

调用方只需要说「我要调 star-site-biz-health」，Nacos 注册中心负责把这个名字
解析为当前可用的 IP:端口列表。如果某个实例挂了，Nacos 会把它从列表中移除，
调用方自动路由到其他健康实例。

本服务的注册流程：
1. 启动时向 Nacos 发送注册请求：「我叫 star-site-platform-ai，地址 star-ai:8091」
2. 之后每 5 秒发送一次「心跳」，告诉 Nacos"我还活着"
3. 如果 Nacos 超过 15 秒没收到心跳 → 把实例标记为不健康
4. 如果超过 30 秒 → 从服务列表中删除，调用方不会再路由到它

技术选型说明：直接调用 Nacos 的 HTTP Open API（v1 接口），而不是用官方 Python SDK。
原因：nacos-sdk-python 的 API 设计和版本兼容性不够稳定，而 v1 Open API 只需要
httpx 发几个 HTTP 请求，实现简单、可控、无额外依赖。
"""

# asyncio：Python 的异步 I/O 框架（FastAPI 底层基于此）
import asyncio
# uuid：生成全局唯一 ID（心跳去重用）
import uuid

# httpx：现代 Python HTTP 客户端（支持 async/await，比 requests 更适合异步场景）
import httpx

# 导入配置单例（Nacos 地址、服务名、端口等）
from app.config import settings

# 心跳任务的全局引用。
# 为什么需要全局引用：asyncio.create_task() 创建的任务如果没有被变量引用，
# 可能在执行前被 Python 垃圾回收器（GC）回收掉——这是 asyncio 的常见坑。
_beat_task: asyncio.Task | None = None


async def register() -> bool:
    """向 Nacos 注册中心注册当前服务实例（幂等操作，可重复调用）。

    注册成功后，Nacos 会将 ``star-ai:8091`` 加入 ``star-site-platform-ai``
    服务的可用实例列表，其他微服务就能通过服务名发现并调用本服务。

    「幂等」的含义：重复调用不会产生副作用（Nacos 会覆盖同 IP+端口的注册信息），
    所以即使注册失败后重试多次也是安全的。

    Returns:
        bool: 注册是否成功。
              失败不抛异常（由心跳循环持续重试），只返回 False——
              服务启动时 Nacos 可能还没就绪，不应该因此阻塞启动流程。
    """
    try:
        # 使用短生命周期的 AsyncClient 发送注册请求
        # （async with 确保请求完成后自动关闭连接）
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                # Nacos v1 Open API：注册服务实例
                f"http://{settings.nacos_server_addr}/nacos/v1/ns/instance",
                params={
                    # 服务名：调用方通过这个名字发现我们
                    "serviceName": settings.service_name,
                    # 注册 IP：Docker Compose 里填容器名（如 star-ai），
                    # 这样同一网络内的其他容器才能通过 DNS 解析到本实例
                    "ip": settings.nacos_register_ip,
                    # 监听端口
                    "port": settings.port,
                    # Nacos 命名空间（服务隔离）
                    "namespaceId": settings.nacos_namespace,
                    # Nacos 分组
                    "groupName": settings.nacos_group,
                    # 临时实例：Nacos 超时收不到心跳会自动删除（ permanent=false 的意思）
                    "ephemeral": "true",
                },
            )
            # HTTP 200 = 注册成功
            success = resp.status_code == 200
            if success:
                print(
                    f"[nacos] 注册成功: {settings.service_name} "
                    f"{settings.nacos_register_ip}:{settings.port}"
                )
            else:
                print(f"[nacos] 注册失败: status={resp.status_code} body={resp.text[:200]}")
            return success
    except Exception as exc:
        # 注册失败不抛异常——可能 Nacos 还没就绪（启动顺序问题），
        # 心跳循环会持续重试，服务不应该因为注册中心暂时不可用而阻塞启动
        print(f"[nacos] 注册异常（心跳将持续重试）: {exc}")
        return False


async def _beat_loop() -> None:
    """心跳循环：每 5 秒上报一次心跳，证明本实例仍然存活。

    Nacos 的健康检查机制（初学者必读）：

    - 每个实例必须定期向 Nacos 发送「心跳」（一个 HTTP PUT 请求），
      告诉 Nacos "我还活着，请继续把请求路由给我"；
    - Nacos 的摘除时间线：
      - 15 秒收不到心跳 → 标记实例为不健康（不再路由新请求）
      - 30 秒收不到心跳 → 从服务列表中删除实例
    - 本服务每 5 秒发一次心跳，留了 3 倍余量（即使偶尔丢一两次也不会被摘除）。

    自愈设计：如果心跳请求发现实例尚未注册（code=20404），会自动触发重新注册——
    即使服务启动时 Nacos 还没就绪，也能在后来自动注册成功。
    """
    # 心跳 ID：每个心跳周期生成一个唯一 ID（Nacos 用它去重心跳请求）
    beat_id = uuid.uuid4().hex

    # 创建长生命周期的 HTTP 客户端，复用连接池（避免每次心跳都重新建 TCP 连接）
    async with httpx.AsyncClient(timeout=5) as client:
        # 无限循环：只要服务活着就持续发心跳
        while True:
            try:
                # 发送心跳请求（HTTP PUT）
                resp = await client.put(
                    # Nacos v1 心跳接口
                    f"http://{settings.nacos_server_addr}/nacos/v1/ns/instance/beat",
                    params={
                        "serviceName": settings.service_name,
                        "namespaceId": settings.nacos_namespace,
                        "groupName": settings.nacos_group,
                        "ephemeral": "true",
                    },
                    # 心跳请求体：告诉 Nacos "这个实例还活着"
                    json={
                        "serviceName": settings.service_name,
                        "ip": settings.nacos_register_ip,
                        "port": settings.port,
                        "beatId": beat_id,
                    },
                )

                if resp.status_code == 200:
                    # 解析响应体中的 code 字段：
                    #   code=10200 → 心跳正常（实例已注册）
                    #   code=20404 → 实例不存在（Nacos 重启后丢了注册信息）
                    #     ⚠️ 关键坑：实例不存在时心跳 HTTP 仍返回 200，
                    #     但 body 里 code=20404——必须检查 body 并自行重新注册，
                    #     否则 Nacos 会以为实例还在但心跳正常（实际已经脱管）
                    body = resp.json()
                    if body.get("code") not in (0, 10200):
                        print(f"[nacos] 心跳未命中实例（code={body.get('code')}），触发重注册")
                        await register()
                else:
                    print(f"[nacos] 心跳异常应答: status={resp.status_code}")
                    await register()

            except Exception as exc:
                # 网络异常：尝试重新注册（可能 Nacos 重启后丢失了实例信息）
                print(f"[nacos] 心跳异常: {exc}")
                await register()

            # 每 5 秒发一次心跳（Nacos 默认 15 秒超时，5 秒间隔留了 3 倍余量）
            await asyncio.sleep(5)


def start_background_register() -> None:
    """启动后台注册/心跳任务（由 FastAPI 的 lifespan 在应用启动时调用）。

    asyncio 任务生命周期说明：
    - ``asyncio.create_task()`` 在当前事件循环上创建一个后台协程任务；
    - 任务会独立于主逻辑持续运行（每 5 秒发一次心跳），直到服务关闭；
    - 必须在「运行中的事件循环」内调用——FastAPI 的 lifespan 回调满足此条件，
      因为 lifespan 本身就是在一个异步上下文中执行的。
    """
    # _beat_task 是模块级变量，防止任务被 Python 垃圾回收器回收
    # （asyncio.create_task 创建的任务如果没有被变量引用，可能在执行前被 GC 掉）
    global _beat_task

    # get_running_loop() 确认当前在事件循环内（lifespan 回调满足此条件），
    # 然后在循环上创建后台任务
    loop = asyncio.get_running_loop()

    # 创建后台任务：注册 + 周期心跳
    _beat_task = loop.create_task(_beat_loop())

    print(f"[nacos] 心跳任务已挂载: {settings.service_name} → {settings.nacos_server_addr}")
