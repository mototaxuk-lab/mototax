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
    create_engine, inspect, text, String, Float, DateTime, ForeignKey, Integer
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

    # Onboarding profile (set during the two-question WhatsApp onboarding).
    # vehicle_type: car_van | motorbike | bicycle  (drives the mileage rate)
    vehicle_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # tax_rate: 0.20 | 0.40 | 0.0  (used only for rough tax-benefit estimates)
    tax_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # onboarding_step: ask_vehicle | ask_tax | done
    onboarding_step: Mapped[str] = mapped_column(String(16), default="ask_vehicle")

    # Flow C (vehicle settings). vehicle_type above is the *main* vehicle.
    # default_vehicle: assumed when mileage is sent without a vehicle (falls back to main)
    default_vehicle: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # extra_vehicles: comma-separated canonical keys the user also has (excludes main)
    extra_vehicles: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # settings_state: where the user is in the settings menu (carries payload after ':')
    settings_state: Mapped[str | None] = mapped_column(String(48), nullable=True)

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
    # Vehicle this mileage entry was logged against (car_van | motorbike | bicycle).
    # Stamped at log time so historical rates survive an active-vehicle switch.
    vehicle_type: Mapped[str | None] = mapped_column(String(16), nullable=True)

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
    """Create tables if they don't exist, then add any missing columns.

    Safe to call on every boot. `create_all` only creates missing *tables*, so
    we add new columns to the existing `users` table by hand (a lightweight
    migration that works on both SQLite and Postgres).
    """
    Base.metadata.create_all(engine)
    _ensure_columns("users", _USER_ADDED_COLUMNS)
    _ensure_columns("records", _RECORD_ADDED_COLUMNS)


_FLOAT_SQL = "FLOAT" if engine.url.drivername.startswith("sqlite") else "DOUBLE PRECISION"

# Columns added after the initial schema, with the SQL type used by ALTER TABLE.
_USER_ADDED_COLUMNS = {
    "vehicle_type": "VARCHAR(16)",
    "tax_rate": _FLOAT_SQL,
    "onboarding_step": "VARCHAR(16) DEFAULT 'ask_vehicle'",
    "default_vehicle": "VARCHAR(16)",
    "extra_vehicles": "VARCHAR(64)",
    "settings_state": "VARCHAR(48)",
}
_RECORD_ADDED_COLUMNS = {
    "vehicle_type": "VARCHAR(16)",
}


def _ensure_columns(table: str, wanted: dict[str, str]) -> None:
    existing = {c["name"] for c in inspect(engine).get_columns(table)}
    missing = {k: v for k, v in wanted.items() if k not in existing}
    if not missing:
        return
    with engine.begin() as conn:
        for name, ddl in missing.items():
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


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


def latest_editing(db: Session, user_id: int) -> Record | None:
    """The record the user is currently correcting (status 'editing'), if any."""
    return (
        db.query(Record)
        .filter_by(user_id=user_id, confirmation_status="editing")
        .order_by(Record.created_at.desc())
        .first()
    )


def latest_awaiting_vehicle(db: Session, user_id: int) -> Record | None:
    """A mileage record waiting for the user to pick which vehicle it was on."""
    return (
        db.query(Record)
        .filter_by(user_id=user_id, confirmation_status="awaiting_vehicle")
        .order_by(Record.created_at.desc())
        .first()
    )


def make_export_link(db: Session, user_id: int) -> str:
    token = secrets.token_urlsafe(24)
    db.add(ExportLink(token=token, user_id=user_id))
    db.commit()
    return token
