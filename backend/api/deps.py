"""
API 依赖注入
"""
import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.core.github_audit import github_actor, github_feature
from infrastructure.core.security import decode_token
from infrastructure.db.base import get_db
from infrastructure.persistence.models import TokenBlacklist, User

logger = logging.getLogger(__name__)

security = HTTPBearer()


async def identify_github_feature(request: Request) -> None:
    module = getattr(request.scope.get("endpoint"), "__module__", None)
    if module:
        github_feature.set(module.rsplit(".", 1)[-1][:40])


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)]
) -> User:
    """获取当前登录用户"""
    token = credentials.credentials
    payload = decode_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("token_type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="必须使用访问Token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jti = payload.get("jti")
    if jti:
        try:
            stmt = select(TokenBlacklist).where(TokenBlacklist.token_jti == jti)
            result = await db.execute(stmt)
            if result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token 已被撤销",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Token blacklist check failed, skipping: {e}")

    username: str = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效",
        )

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )

    github_actor.set(username)
    return user


async def get_current_active_admin_user(
    current_user: Annotated[User, Depends(get_current_user)]
) -> User:
    """获取当前登录的管理员用户"""
    if current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足",
        )
    return current_user


async def get_current_active_super_admin_user(
    current_user: Annotated[User, Depends(get_current_user)]
) -> User:
    """获取当前登录的超级管理员用户"""
    if current_user.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足",
        )
    return current_user


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentAdminUser = Annotated[User, Depends(get_current_active_admin_user)]
CurrentSuperAdminUser = Annotated[User, Depends(get_current_active_super_admin_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
