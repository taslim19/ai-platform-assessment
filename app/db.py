from datetime import datetime, timezone
import uuid
from sqlalchemy import String, Text, Integer, Boolean, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    __table_args__ = (CheckConstraint("role IN ('admin','user','readonly')", name='valid_user_role'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default='user')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ChatRecord(Base):
    __tablename__ = 'chat_records'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    request_id: Mapped[str] = mapped_column(String(36), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    model: Mapped[str] = mapped_column(String(256))
    prompt_tokens: Mapped[int] = mapped_column(Integer)
    completion_tokens: Mapped[int] = mapped_column(Integer)
    total_tokens: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    cached: Mapped[bool] = mapped_column(Boolean)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, default=lambda: datetime.now(timezone.utc))


def database(settings):
    kwargs = {'pool_pre_ping': True, 'hide_parameters': True, 'echo': False}
    if settings.database_url.startswith('postgresql'):
        kwargs.update(pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow,
                      pool_timeout=5, connect_args={'timeout': 5, 'command_timeout': 10})
    engine = create_async_engine(settings.database_url, **kwargs)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
