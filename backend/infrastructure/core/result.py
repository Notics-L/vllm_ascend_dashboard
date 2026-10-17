"""
统一响应与业务结果类型

RESTful 响应规范：
- 接口层（API Router）：通过 Result 包装返回标准化 JSON
- 业务层（Service）：返回 Result[T] 而非直接 raise HTTPException
- 模型层（Model）：提供静态 CRUD 方法，只操作数据不抛出业务异常

响应格式：
    {"code": 200, "msg": "success", "data": {}}
"""
from __future__ import annotations

import logging
from typing import Any, Generic, TypeVar

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from infrastructure.core.exceptions import StatusCode

T = TypeVar("T")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 统一响应类 Result[T]
# ---------------------------------------------------------------------------


class Result(Generic[T]):
    """统一响应结果类，贯穿三层架构。"""

    def __init__(
        self,
        data: T | None = None,
        code: int = 200,
        msg: str = "success",
    ):
        self.data = data
        self.code = code
        self.msg = msg

    # ── 工厂方法（由 StatusCode 枚举驱动）──

    @classmethod
    def ok(cls, data: T | None = None, msg: str = "success") -> Result[T]:
        return cls(data=data, code=int(StatusCode.OK), msg=msg)

    @classmethod
    def created(cls, data: T | None = None, msg: str = "created") -> Result[T]:
        return cls(data=data, code=int(StatusCode.CREATED), msg=msg)

    @classmethod
    def no_content(cls) -> Result[None]:
        return cls(data=None, code=int(StatusCode.NO_CONTENT), msg="no content")

    @classmethod
    def error(
        cls,
        code: int | StatusCode,
        msg: str = "",
        data: T | None = None,
    ) -> Result[T]:
        if isinstance(code, StatusCode):
            return cls(data=data, code=int(code), msg=msg or code.default_msg)
        return cls(data=data, code=code, msg=msg or "error")

    @classmethod
    def not_found(cls, msg: str = "") -> Result[T]:
        return cls.error(StatusCode.NOT_FOUND, msg, None)

    @classmethod
    def bad_request(cls, msg: str) -> Result[T]:
        return cls.error(StatusCode.BAD_REQUEST, msg, None)

    @classmethod
    def conflict(cls, msg: str) -> Result[T]:
        return cls.error(StatusCode.CONFLICT, msg, None)

    @classmethod
    def forbidden(cls, msg: str = "") -> Result[T]:
        return cls.error(StatusCode.FORBIDDEN, msg, None)

    @classmethod
    def unauthorized(cls, msg: str = "") -> Result[T]:
        return cls.error(StatusCode.UNAUTHORIZED, msg, None)

    @classmethod
    def server_error(cls, msg: str = "") -> Result[T]:
        return cls.error(StatusCode.INTERNAL, msg, None)

    # ── 便捷构造：枚举直调 Result ──────────────────────────────

    @staticmethod
    def from_status(status: StatusCode, msg: str = "", data: Any = None) -> Result[Any]:
        """从 StatusCode 枚举直接构造 Result。"""
        return Result(data=data, code=int(status), msg=msg or status.default_msg)

    # ── 属性与方法 ────────────────────────────────────────────

    def is_ok(self) -> bool:
        return 200 <= self.code < 300

    def is_err(self) -> bool:
        return not self.is_ok()

    def to_response(self, status_code: int | None = None) -> JSONResponse:
        """转换为 FastAPI JSONResponse（接口层调用）。"""
        return JSONResponse(
            status_code=status_code or 200,
            content={"code": self.code, "msg": self.msg, "data": self.data},
        )

    def to_http_exception(self) -> HTTPException:
        """将错误结果转为 HTTPException（业务层调用）。"""
        if self.is_ok():
            return HTTPException(status_code=200, detail="unexpected success in error path")
        return HTTPException(status_code=self.code, detail=self.msg)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "msg": self.msg, "data": self.data}


# ---------------------------------------------------------------------------
# 分页响应
# ---------------------------------------------------------------------------


class PaginatedResult(Result[list[T]]):
    """分页结果"""

    @classmethod
    def ok(
        cls,
        items: list[T],
        total: int,
        page: int = 1,
        page_size: int = 20,
        msg: str = "success",
    ) -> PaginatedResult[T]:
        pages = (total + page_size - 1) // page_size if page_size > 0 else 0
        return cls(
            data=items,
            code=int(StatusCode.OK),
            msg=msg,
        )._with_pagination(total, page, page_size, pages)

    def _with_pagination(
        self, total: int, page: int, page_size: int, pages: int
    ) -> PaginatedResult[T]:
        self.pagination = {
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
        }
        return self

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base["pagination"] = getattr(self, "pagination", None)
        return base

    def to_response(self, status_code: int | None = None) -> JSONResponse:
        return JSONResponse(
            status_code=status_code or 200,
            content=self.to_dict(),
        )
