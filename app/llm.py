"""
LLM 模型工厂：按需创建 LangChain 对话模型实例（文本 / 视觉）。

LangChain 模型层概念（初学者必读）：
- LangChain 把各种 LLM（OpenAI / 智谱 / Anthropic…）统一抽象为 ``BaseChatModel``，
  无论底层是哪家厂商，调用方式都一样（``.invoke(messages)`` / ``.ainvoke(messages)``）；
- ``ChatOpenAI`` 是针对 OpenAI 兼容协议的实现——只要服务支持 OpenAI 的
  ``/v1/chat/completions`` 接口，改 ``base_url`` 和 ``model`` 就能切换厂商，
  代码零改动（这就是"模型无关"设计的核心）；
- LCEL（LangChain Expression Language）：用 ``|`` 管道符把 prompt → model → parser
  串成链（chain），类似 Java Stream 的 ``map → filter → collect``。

模型工厂的设计模式：
- ``@lru_cache`` 缓存已创建的实例——ChatOpenAI 初始化涉及 HTTP 客户端建立
  和密钥校验，代价不低；同一进程内复用同一个实例即可（线程安全 ✓）；
- ``vision=True`` 参数区分文本模型和视觉模型：
  - 文本模型（glm-4.5-air）：处理纯文本对话、报告解读、财务诊断；
  - 视觉模型（glm-4v-flash）：处理拍照 OCR——需要「看图」理解报告内容。
  为什么不能共用：文本模型只接受文字输入，图片必须传给支持视觉的模型。
"""

from functools import lru_cache

from langchain_openai import ChatOpenAI

# 导入配置单例（模块级导入，启动时解析一次环境变量）
from app.config import settings


@lru_cache(maxsize=4)
def chat_model(vision: bool = False) -> ChatOpenAI:
    """创建（或从缓存获取）LangChain 对话模型实例。

    ``@lru_cache(maxsize=4)`` 装饰器：
        LRU（Least Recently Used）缓存——相同参数只创建一次实例，
        后续调用直接返回缓存结果。maxsize=4 足够覆盖 (vision=True/False) 的组合。

    Args:
        vision: 是否使用视觉（多模态）模型。
                - ``False``（默认）：文本对话模型，用于报告解读、财务诊断、通用对话；
                - ``True``：视觉模型，用于拍照 OCR 等需要理解图片的场景。

    Returns:
        ChatOpenAI: LangChain 对话模型实例，
                    可直接用 ``|`` 管道符串联成 LCEL 链，
                    或传入 ``create_react_agent`` 作为智能体的"大脑"。

    LangChain 使用示例::

        # 最简单的 LCEL 链：prompt 模板 → 模型 → 文本解析器
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        prompt = ChatPromptTemplate.from_messages([
            ("system", "你是家庭健康助手"),
            ("human", "{question}"),
        ])
        chain = prompt | chat_model() | StrOutputParser()
        answer = chain.invoke({"question": "感冒了怎么办"})
    """
    return ChatOpenAI(
        # ===== OpenAI 兼容协议三要素 =====
        # API 基地址：智谱 GLM 的 OpenAI 兼容端点
        # （也支持 OpenAI / DeepSeek / 百川等任何实现了此协议的服务）
        base_url=settings.llm_base_url,
        # API 密钥（沙箱模式传占位符 "mock-key"——构造函数要求非空，但沙箱不真调）
        api_key=settings.llm_api_key or "mock-key",
        # 模型名：决定用文本模型还是视觉模型
        model=settings.llm_vision_model if vision else settings.llm_model,

        # ===== 生成参数 =====
        # temperature=0.2：低随机性。
        # 值域 0~1，越低输出越确定/一致，越高越发散/有创意。
        # 健康和财务场景要求稳定输出（同样的输入应该得到类似的回答），不要发散。
        temperature=0.2,
        # 单次请求超时 60 秒——LLM 推理可能较慢，尤其是长文本
        timeout=60,
        # 网络抖动时自动重试 1 次（LangChain 内置重试机制）
        max_retries=1,
    )
