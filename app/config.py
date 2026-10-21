"""
服务配置模块 —— 全部配置项通过环境变量注入，代码中不出现任何密钥或环境相关硬编码。

为什么用 dataclass(frozen=True) 而不是普通类或 dict：
- ``frozen=True`` 让实例不可变（尝试修改会抛异常），保证配置在运行期安全；
- dataclass 自动生成 ``__init__`` / ``__repr__`` / ``__eq__``，省去样板代码；
- IDE 能自动补全字段名，写错会立刻标红（dict 的 key 写错只有运行时才发现）。

为什么用模块级单例 ``settings = Settings()``：
- 整个进程只需要一份配置，创建一次后全局复用；
- 各模块 ``from app.config import settings`` 导入的是同一个实例，
  修改环境变量后重启服务即可生效，代码不用改。

环境变量命名规范（12-Factor App 第三条原则）：
- 所有配置通过环境变量注入（Docker Compose / Kubernetes 标准），支持不同环境（开发/测试/生产）
  使用不同配置而不需要改代码；
- 敏感信息（API 密钥、数据库密码）绝不写死在代码或配置文件里。
"""

# ---- 标准库导入 ----
import os
from dataclasses import dataclass


def _env(key: str, default: str = "") -> str:
    """读取环境变量，存在但为空串时也视为未设置。

    为什么要这样处理：
        Docker Compose / Kubernetes 中很容易出现 ``ENV_VAR=``（等号后面为空）的情况。
        ``os.environ.get(key)`` 会返回空字符串而不是 None，
        导致「已设置」的假象——统一在这里把空串兜底为 default。

    处理流程：
        1. ``os.environ.get(key)`` 从进程环境变量中取值；
        2. 如果取到了值 → ``strip()`` 去掉首尾空白（可能是引号残留），直接返回；
        3. 如果没取到（None）或为空串 → 返回 default。

    Args:
        key:     环境变量名，如 ``"AI_LLM_API_KEY"``。
        default: 未设置时的默认值（调用方传入）。

    Returns:
        str: 环境变量的值（已去空白），或调用方指定的 default。

    使用示例::

        >>> _env("AI_PORT", "8091")     # 环境变量 AI_PORT 未设置
        '8091'                           # 返回 default "8091"
        >>> _env("AI_PORT", "8091")     # 环境变量 AI_PORT="9090"
        '9090'                           # 返回实际值 "9090"
    """
    # 从进程环境变量中取值（可能返回 None 或空串）
    value = os.environ.get(key)

    # 去掉首尾空白（引号残留、多余空格等）
    if value is not None:
        value = value.strip()

    # 空串视为未设置，回落到 default
    return value if value else default


@dataclass(frozen=True)  # frozen=True：实例不可变，防止运行期意外修改配置
class Settings:
    """进程级只读配置。

    每个 Field 的含义和取值来源：

    Attributes:
        service_name:      注册到 Nacos 的服务名（也是日志里的 app 标识）。
        port:              HTTP 监听端口（uvicorn 绑定用）。
        nacos_server_addr: Nacos 注册中心地址，格式 ``host:port``。
        nacos_namespace:   Nacos 命名空间（不同命名空间的服务互相不可见，用于环境隔离）。
        nacos_register_ip: 注册到 Nacos 的 IP。
                           Docker Compose 里填容器名（如 ``star-ai``），
                           这样同一网络内的其他容器才能通过服务名解析到本实例。
        nacos_group:       Nacos 分组（默认 DEFAULT_GROUP，一般不改）。
        jwt_secret:        JWT 签名密钥（HS256 对称加密）。
                           必须与网关 / Java 服务保持一致，否则验签会失败。
                           生产环境务必通过环境变量注入，不要写死在代码里。
        llm_base_url:      LLM 的 OpenAI 兼容 API 地址。
                           智谱 GLM: ``https://open.bigmodel.cn/api/paas/v4``
                           OpenAI:   ``https://api.openai.com/v1``
                           DeepSeek: ``https://api.deepseek.com/v1``
        llm_api_key:       LLM API 密钥。为空时自动进入沙箱模式（不调真实模型、零费用）。
        llm_model:         文本对话模型名（处理报告解读、财务诊断、对话等纯文本任务）。
        llm_vision_model:  视觉（多模态）模型名——拍照 OCR 需要「看图」能力，
                           文本模型（如 glm-4.5-air）不接受图片输入，必须用视觉模型。
    """

    # ==================== 服务标识 ====================
    # 注册到 Nacos 的服务名，其他微服务通过这个名字找到我们
    service_name: str = _env("SERVICE_NAME", "star-site-platform-ai")

    # HTTP 监听端口（uvicorn 启动时绑定）
    port: int = int(_env("AI_PORT", "8091"))

    # ==================== Nacos 服务注册与发现 ====================
    # Nacos 注册中心地址（host:port 格式）
    # Docker Compose 里用容器名 star-nacos:8848；本地开发用 127.0.0.1:8848
    nacos_server_addr: str = _env("NACOS_SERVER_ADDR", "127.0.0.1:8848")

    # Nacos 命名空间（不同命名空间的服务互相隔离，用于区分开发/测试/生产）
    nacos_namespace: str = _env("NACOS_NAMESPACE", "public")

    # 注册到 Nacos 的 IP。
    # Docker Compose 里填容器名（如 star-ai），这样同一网络的容器才能路由到我们。
    # 本地开发不设置则默认 127.0.0.1
    nacos_register_ip: str = _env("NACOS_REGISTER_IP", "127.0.0.1")

    # Nacos 分组（一般用默认值 DEFAULT_GROUP 不改）
    nacos_group: str = _env("NACOS_GROUP", "DEFAULT_GROUP")

    # ==================== JWT 验签 ====================
    # JWT 签名密钥（HS256 对称加密），必须与网关和 Java 服务保持一致。
    # 用途：/api/ai/agent/** 端点收到请求后，自行验签 JWT 拿到 memberId，
    #       让智能体工具以「当前登录用户」的身份查询业务数据（而不是匿名查询）。
    jwt_secret: str = _env("JWT_SECRET", "star-site-local-dev-secret-change-me-32bytes!")

    # ==================== LLM 模型接入 ====================
    # LLM 的 OpenAI 兼容 API 基地址
    # 兼容所有 OpenAI 协议的 LLM 服务：智谱 / OpenAI / DeepSeek / 百川……
    llm_base_url: str = _env("AI_LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")

    # API 密钥。为空时自动进入沙箱模式（不调真实模型、零费用）
    llm_api_key: str = _env("AI_LLM_API_KEY", "")

    # 文本对话模型名（处理报告解读、财务诊断、通用对话等纯文本任务）
    llm_model: str = _env("AI_LLM_MODEL", "glm-4.5-air")

    # 视觉（多模态）模型名——拍照 OCR 需要「看图」能力。
    # 为什么不能共用文本模型：文本模型（如 glm-4.5-air）只接受文字输入，
    # 图片必须传给支持视觉的模型（如 glm-4v-flash）才能理解。
    llm_vision_model: str = _env("AI_LLM_VISION_MODEL", "glm-4v-flash")

    def _explicit_mock(self) -> str:
        """读取 AI_MOCK_ENABLED 环境变量（统一转小写，方便比较 true/false/yes/no）。"""
        return _env("AI_MOCK_ENABLED", "").lower()

    @property
    def effective_mock(self) -> bool:
        """判断当前是否处于沙箱模式（不调真实 LLM，返回固定模拟数据）。

        优先级（从高到低）：

        1. 环境变量 ``AI_MOCK_ENABLED=true``  → 强制沙箱（不调模型、零费用）
        2. 环境变量 ``AI_MOCK_ENABLED=false`` 且有 key → 真实模式
        3. 未设置 → 有 key 走真实模式，无 key 自动沙箱

        沙箱模式的价值：
            没有 LLM 密钥也能跑通全链路（返回固定模拟数据），
            让开发 / 联调 / 演示不因缺密钥而阻塞。

        Returns:
            bool: True = 沙箱模式，False = 真实模式。
        """
        # 读取显式设置的开关值（转小写统一比较）
        explicit = self._explicit_mock()

        # 显式开启沙箱
        if explicit in ("true", "1", "yes"):
            return True

        # 显式关闭沙箱
        if explicit in ("false", "0", "no"):
            # 但如果连 API key 都没有，即使关了沙箱也调不了模型 → 仍回沙箱
            return not self.llm_api_key

        # 未显式设置：有 key 走真实模式，无 key 自动沙箱
        return not self.llm_api_key


# ===== 模块级单例 =====
# 全项目统一从这一行导入配置，避免重复解析环境变量。
# 其他模块的用法：from app.config import settings → settings.port / settings.llm_api_key
settings = Settings()
