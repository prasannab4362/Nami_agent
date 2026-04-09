import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./users.db")

# Render / Heroku may provide postgres://, but SQLAlchemy expects postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    bind=engine,
)

Base = declarative_base()


def run_migrations():
    """Apply lightweight schema fixes for existing databases."""
    with engine.connect() as conn:
        inspector = inspect(conn)
        existing_columns = {column["name"] for column in inspector.get_columns("users")}
        if "phone" not in existing_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR"))

        existing_indexes = {
            index["name"] for index in inspector.get_indexes("users")
            if index.get("name")
        }
        if "ix_users_phone" not in existing_indexes:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_phone "
                    "ON users(phone) WHERE phone IS NOT NULL"
                )
            )

        conn.commit()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
