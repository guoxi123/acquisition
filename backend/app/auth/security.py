"""密码加密（bcrypt）+ JWT token 签发/校验。"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt as pyjwt

from app.core.config import settings
from app.utils.logger import logger


def hash_password(password: str) -> str:
    logger.debug("密码哈希")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_access_token(data: dict) -> str:
    logger.debug(f"签发 token: sub={data.get('sub')}")
    to_encode = {
        **data,
        "exp": datetime.now(timezone.utc)
        + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return pyjwt.encode(to_encode, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    try:
        return pyjwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except pyjwt.PyJWTError as e:
        logger.warning(f"token 校验失败: {e}")
        return None
