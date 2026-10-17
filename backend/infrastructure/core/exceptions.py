"""
业务异常与状态码枚举

状态码枚举 StatusCode 统一管理所有业务状态码，可直接用于构造 Result 响应。
"""
from __future__ import annotations

from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# 状态码枚举
# ---------------------------------------------------------------------------


class StatusCode(int, Enum):
    """全局状态码枚举，同时携带 HTTP 状态码和默认消息。"""

    # ── 成功 2xx ──────────────────────────────────────────────
    OK = (200, "success")
    CREATED = (201, "created")
    NO_CONTENT = (204, "no content")

    # ── 客户端错误 4xx ────────────────────────────────────────
    BAD_REQUEST = (400, "bad request")
    UNAUTHORIZED = (401, "unauthorized")
    FORBIDDEN = (403, "forbidden")
    NOT_FOUND = (404, "not found")
    METHOD_NOT_ALLOWED = (405, "method not allowed")
    CONFLICT = (409, "conflict")
    UNPROCESSABLE_ENTITY = (422, "unprocessable entity")
    RATE_LIMIT = (429, "rate limit exceeded")

    # ── 服务端错误 5xx ────────────────────────────────────────
    INTERNAL = (500, "internal server error")
    SERVICE_UNAVAILABLE = (503, "service unavailable")
    GATEWAY_TIMEOUT = (504, "gateway timeout")

    def __new__(cls, http_code: int, default_msg: str):
        obj = int.__new__(cls, http_code)
        obj._value_ = http_code
        obj.http_code = http_code
        obj.default_msg = default_msg
        return obj

    def __init__(self, http_code: int, default_msg: str):
        self.http_code = http_code
        self.default_msg = default_msg

    def with_msg(self, msg: str) -> tuple[int, str]:
        """返回 (code, msg) 元组，用于构造 Result"""
        return int(self), msg

    @classmethod
    def from_http_status(cls, code: int) -> "StatusCode":
        """从 HTTP 状态码反查枚举，找不到则返回 INTERNAL"""
        for member in cls:
            if member.http_code == code:
                return member
        return cls.INTERNAL


# ---------------------------------------------------------------------------
# 业务异常类
# ---------------------------------------------------------------------------


class BusinessException(Exception):
    """业务异常基类"""

    status_code: StatusCode = StatusCode.BAD_REQUEST

    def __init__(self, msg: str, detail: Any | None = None):
        super().__init__(msg)
        self.msg = msg
        self.detail = detail


class NotFoundError(BusinessException):
    """资源不存在"""
    status_code = StatusCode.NOT_FOUND


class ValidationError(BusinessException):
    """参数校验失败"""
    status_code = StatusCode.BAD_REQUEST


class ConflictError(BusinessException):
    """资源冲突（重复提交）"""
    status_code = StatusCode.CONFLICT


class ForbiddenError(BusinessException):
    """权限不足"""
    status_code = StatusCode.FORBIDDEN


class UnauthorizedError(BusinessException):
    """未认证"""
    status_code = StatusCode.UNAUTHORIZED


class ExternalServiceError(BusinessException):
    """外部服务不可用"""
    status_code = StatusCode.SERVICE_UNAVAILABLE


class RateLimitError(BusinessException):
    """频率限制"""
    status_code = StatusCode.RATE_LIMIT
