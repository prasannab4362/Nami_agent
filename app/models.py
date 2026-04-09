from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from datetime import datetime
from app.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    phone = Column(String, unique=True, nullable=True)
    access_token = Column(String)
    refresh_token = Column(String)


class UserMemory(Base):
    __tablename__ = "user_memory"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    key = Column(String)    # e.g. "name", "company", "preference"
    value = Column(String)


class ConversationHistory(Base):
    __tablename__ = "conversation_history"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    role = Column(String)    # "user" or "assistant"
    message = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
