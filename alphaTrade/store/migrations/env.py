import logging
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Import models so SQLModel metadata is populated for autogenerate
import alphaTrade.store.repos  # noqa: F401
from sqlmodel import SQLModel

config = context.config

if config.config_file_name is not None and not logging.getLogger().handlers:
    # Only apply alembic.ini logging when no handlers are configured (CLI usage).
    # Skip when embedded in the app — the app configures its own JSON handlers.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
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
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
