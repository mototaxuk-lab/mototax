"""Database layer: SQLAlchemy 2.0 models + session helpers.

Tables
------
users          one row per courier, keyed by WhatsApp number
records        one row per income / expense / mileage entry (the ledger)
export_links   short-lived random tokens that map to a user for CSV download
"""
import datetime as dt
import secrets

from sqlalchemy import (
    create_engine, String, Float, DateTime, ForeignKey, Integer
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, Session
)

import config

# Railway hands out URLs starting "postgres://"; SQLAlchemy wants "postgresql://".
db_url = config.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

# SQLite needs a special flag for multi-threaded access (FastAPI uses threads).
connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
engine = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    whatsapp_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    records: Mapped[list["Record"]] = relationship(back_populates="user")


class Record(Base):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # record_type: income | expense | mileage
    record_type: Mapped[str] = mapped_column(String(16))
    record_date: Mapped[str] = mapped_column(String(10))  # ISO yyyy-mm-dd
    platform_or_vendor: Mapped[str] = mapped_column(String(64), default="")
    category: Mapped[str] = mapped_column(String(32), default="")

    amount: Mapped[float | None] = mapped_column(Float, nullable=True)  # GBP
    miles: Mapped[float | None] = mapped_column(Float, nullable=True)

    # source_type: screenshot | receipt_photo | text_entry | odometer_photo | user_estimate
    source_type: Mapped[str] = mapped_column(String(24), default="")
    # confirmation_status: pending | confirmed | rejected | estimated
    confirmation_status: Mapped[str] = mapped_column(String(16), default="pending")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    original_media_url: Mapped[str] = mapped_column(String(512), default="")
    notes: Mapped[str] = mapped_column(String(512), default="")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="records")


class ExportLink(Base):
    __tablename__ = "export_links"

    token: Mapped[str] = mapped_column(String(48), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every boot."""
    Base.metadata.create_all(engine)


def get_or_create_user(db: Session, whatsapp_number: str) -> tuple[User, bool]:
    """Return (user, created). `created` is True if this is a brand-new user."""
    user = db.query(User).filter_by(whatsapp_number=whatsapp_number).first()
    if user:
        return user, False
    user = User(whatsapp_number=whatsapp_number)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, True


def latest_pending(db: Session, user_id: int) -> Record | None:
    return (
        db.query(Record)
        .filter_by(user_id=user_id, confirmation_status="pending")
        .order_by(Record.created_at.desc())
        .first()
    )


def make_export_link(db: Session, user_id: int) -> str:
    token = secrets.token_urlsafe(24)
    db.add(ExportLink(token=token, user_id=user_id))
    db.commit()
    return token
