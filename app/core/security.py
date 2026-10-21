"""
JWT 身份解析模块。

安全链路说明（初学者必读）：

JWT（JSON Web Token）是一种无状态的身份令牌——用户登录成功后，
网关签发一个包含用户身份信息的 token，之后用户每次请求都带上这个 token。
服务端拿到 token 后验签（确认没被篡改）+ 解析（提取用户 ID）。

验签流程：
1. 用户在登录页登录 → member 服务验证用户名/密码 → 签发 JWT token
2. 用户后续每次请求携带 ``Authorization: Bearer <token>`` 请求头
3. 网关首先验签 → 通过则转发到下游服务
4. 下游服务（如本 AI 服务）再次验签 → 提取 memberId → 以此身份查询数据

为什么 AI 服务要自己验签：
网关已经验过 JWT 了，但网关不会把「用户是谁」传给下游。
AI 服务的智能体工具需要知道「当前用户是谁」才能查询他的数据
（不能查别人的数据！），所以需要自己解析 JWT 拿到 memberId。
HS256 是对称加密——只要 AI 服务和网关使用同一个密钥，就能互相验签。

安全红线：
- JWT 密钥绝不能泄露（泄露 = 任何人都可以伪造 token = 身份认证形同虚设）
- token 有过期时间（exp 字段），过期后 PyJWT 自动拒绝
"""

# PyJWT：Python 的 JWT 编解码库（签发 + 验签 + 解析）
import jwt

# 导入配置单例（读 jwt_secret）
from app.config import settings


class InvalidToken(Exception):
    """token 无效或已过期——调用方应返回 401 未认证。"""


def parse_member_id(token: str) -> int:
    """验签并从 JWT token 中提取用户 ID（memberId）。

    JWT 结构（三段用点号分隔的 Base64URL 编码字符串）::

        header.payload.signature
          │       │        │
          │       │        └── 签名（防篡改，用密钥 + HMAC-SHA256 生成）
          │       └── payload（载荷：sub=memberId, exp=过期时间...）
          └── header（算法声明：{"alg":"HS256","typ":"JWT"}）

    Args:
        token: JWT 字符串（不含 "Bearer " 前缀，调用方负责剥离）。

    Returns:
        int: 用户 ID（对应数据库 member 表的 id 列）。

    Raises:
        InvalidToken: token 缺失、格式错误、签名不匹配或已过期。

    使用示例::

        >>> member_id = parse_member_id("eyJhbGciOi...")
        >>> print(member_id)
        12
    """
    # 空值检查：没有 token 直接拒绝
    if not token:
        raise InvalidToken("缺少 token")

    try:
        # jwt.decode 做了三件事：
        # 1. 验签（用密钥确认 token 没被篡改）
        # 2. 检查过期时间（exp 字段，过期抛 ExpiredSignatureError）
        # 3. 解析 payload 为 dict
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        # PyJWTError 是所有 JWT 相关异常的基类
        # （包括签名不匹配、过期、格式错误等）
        raise InvalidToken(f"token 无效: {exc}") from exc

    # JWT 的 sub（subject）字段存的是用户 ID（Java 侧 JwtUtil.generateToken 写入）
    sub = payload.get("sub")
    if sub is None:
        raise InvalidToken("token 缺少身份声明")

    # sub 在 Java 侧是以字符串形式写入的（String.valueOf(memberId)），
    # 这里转回 int 用于数据库查询
    return int(sub)
