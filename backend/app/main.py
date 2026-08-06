from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api import admin, chat, chat_sessions, chat_stream
from app.auth import api as auth_api
from app.core.config import settings
from app.core.db import async_session


from app.utils.logger import setup_logger

logger = setup_logger(log_dir="logs", console_log_level="INFO", file_log_level="DEBUG")


async def _init_super_admin() -> None:
    """启动时确保超管 guoxi/guoxi 存在。"""
    from app.auth.security import hash_password
    from app.models.user import User

    async with async_session() as db:
        if not (
            await db.execute(select(User).where(User.username == "guoxi"))
        ).scalars().first():
            db.add(
                User(
                    username="guoxi",
                    password_hash=hash_password("guoxi"),
                    is_super_admin=True,
                )
            )
            await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("应用启动中...")
    await _init_super_admin()
    logger.info("应用启动完成，开始接收请求")
    yield
    logger.info("应用关闭")


app = FastAPI(title="Acquisition Agent API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_api.router)
app.include_router(admin.router)
app.include_router(chat.router)
app.include_router(chat_sessions.router)
app.include_router(chat_stream.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}
