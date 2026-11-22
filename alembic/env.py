import asyncio
from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import pool, text
from app.config import Settings
from app.db import Base

config = context.config
target_metadata = Base.metadata


def configure(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = create_async_engine(Settings().database_url, poolclass=pool.NullPool, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            # Serialize releases, so concurrent deployment jobs cannot race schema versions.
            if connection.dialect.name == 'postgresql':
                await connection.execute(text('SELECT pg_advisory_lock(91730421)'))
                await connection.commit()
            try:
                await connection.run_sync(configure)
            finally:
                if connection.dialect.name == 'postgresql':
                    await connection.execute(text('SELECT pg_advisory_unlock(91730421)'))
                    await connection.commit()
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(url=Settings().database_url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={'paramstyle':'named'})
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
