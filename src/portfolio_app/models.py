from __future__ import annotations

import datetime as dt
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, DateTime, Text, Boolean


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_chat_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow())

    # schema mapping hints
    ticker_col: Mapped[str | None] = mapped_column(String, nullable=True)
    qty_col: Mapped[str | None] = mapped_column(String, nullable=True)

    weekly_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)

    filename: Mapped[str] = mapped_column(String)
    stored_path: Mapped[str] = mapped_column(String)
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow())

    # optional: detected delimiter / encoding
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    upload_id: Mapped[int] = mapped_column(Integer, index=True)

    kind: Mapped[str] = mapped_column(String)  # "analysis" or "weekly"
    status: Mapped[str] = mapped_column(String, default="queued")  # queued|running|done|failed
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow())
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_paths_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # list of files