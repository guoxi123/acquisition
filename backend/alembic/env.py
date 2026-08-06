"""Alembic 环境配置：用同步驱动连 PG，autogenerate 基于 app.models metadata。"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# 让 alembic 能 import app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()  # 读 backend/.env

from app.core.db import Base  # noqa: E402
from app.models import *  # noqa: E402,F401,F403  注册所有模型到 metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 用同步 URL（asyncpg -> psycopg2），env 里没配则从 async URL 派生
sync_url = os.getenv("DATABASE_URL_SYNC") or os.getenv(
    "DATABASE_URL", ""
).replace("+asyncpg", "+psycopg2")
config.set_main_option("sqlalchemy.url", sync_url)

target_metadata = Base.metadata


def include_object(object, name, type_, reflected, compare_to):
    """alembic 忽略 LangGraph 运行时表（checkpoints/checkpoint_blobs/checkpoint_writes/checkpoint_migrations）。

    这些表由 PostgresSaver.setup() 运行时创建，不属于 app 模型，autogenerate 不应检测/删除。
    """
    if type_ == "table" and name.startswith("checkpoint"):
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
