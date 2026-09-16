import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = "sqlite+aiosqlite:///./automcdc.db"

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


async def get_db():
    async with SessionLocal() as session:
        yield session


_MIGRATIONS = [
    "ALTER TABLE llm_configs ADD COLUMN base_url TEXT DEFAULT 'http://localhost:11434'",
    "ALTER TABLE test_environments ADD COLUMN preceding_direction TEXT DEFAULT 'sequential'",
    "ALTER TABLE test_environments ADD COLUMN min_initial_random INTEGER DEFAULT 6",
]


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 自動補欄位（忽略已存在的錯誤）
        from sqlalchemy import text
        for sql in _MIGRATIONS:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass
