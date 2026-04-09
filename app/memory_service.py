from sqlalchemy.orm import Session
from app.models import UserMemory, ConversationHistory

MAX_HISTORY = 20  # keep last 20 messages per user


def get_memories(user_id: int, db: Session) -> dict:
    rows = db.query(UserMemory).filter(UserMemory.user_id == user_id).all()
    return {r.key: r.value for r in rows}


def set_memory(user_id: int, key: str, value: str, db: Session):
    existing = db.query(UserMemory).filter(
        UserMemory.user_id == user_id,
        UserMemory.key == key
    ).first()
    if existing:
        existing.value = value
    else:
        db.add(UserMemory(user_id=user_id, key=key, value=value))
    db.commit()


def get_history(user_id: int, db: Session, limit: int = 6):
    rows = (
        db.query(ConversationHistory)
        .filter(ConversationHistory.user_id == user_id)
        .order_by(ConversationHistory.id.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(rows))


def add_to_history(user_id: int, role: str, message: str, db: Session):
    try:
        db.add(ConversationHistory(user_id=user_id, role=role, message=message))
        db.commit()

        # Trim to MAX_HISTORY
        all_msgs = (
            db.query(ConversationHistory)
            .filter(ConversationHistory.user_id == user_id)
            .order_by(ConversationHistory.id.asc())
            .all()
        )
        if len(all_msgs) > MAX_HISTORY:
            for msg in all_msgs[: len(all_msgs) - MAX_HISTORY]:
                db.delete(msg)
            db.commit()
    except Exception:
        db.rollback()
        raise
