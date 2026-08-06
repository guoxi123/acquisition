"""FastAPI 鉴权依赖：从 Bearer token 解析当前用户。"""

import uuid

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.security import decode_access_token
from app.core.db import async_session
from app.models.user import User
from app.utils.logger import logger

bearer_scheme = HTTPBearer()


async def get_current_user(
    cred: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> User:
    payload = decode_access_token(cred.credentials)
    if not payload or "sub" not in payload:
        logger.warning("鉴权失败: 无效或过期 token")
        raise HTTPException(status_code=401, detail="无效或过期 token")
    async with async_session() as db:
        user = await db.get(User, uuid.UUID(payload["sub"]))
    if user is None:
        logger.warning(f"鉴权失败: 用户不存在 sub={payload['sub']}")
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


async def get_super_admin(user: User = Depends(get_current_user)) -> User:
    """仅超管可通过；否则 403。"""
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="仅超管可访问")
    return user
