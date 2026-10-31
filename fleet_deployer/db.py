"""SQLite state: tracked commits, per-host expected systems, host status, jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import ForeignKey, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    # SQLite has no timezone support; store naive UTC and tag it on the way out.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return dt.replace(tzinfo=timezone.utc).isoformat() if dt else None


class Base(DeclarativeBase):
    pass


class Commit(Base):
    __tablename__ = "commits"

    sha: Mapped[str] = mapped_column(String(40), primary_key=True)
    subject: Mapped[str]
    author: Mapped[str]
    committed_at: Mapped[datetime]
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)


class Expected(Base):
    """What `nixosConfigurations.<host>` evaluates to at a given commit."""

    __tablename__ = "expected"

    sha: Mapped[str] = mapped_column(ForeignKey("commits.sha"), primary_key=True)
    host: Mapped[str] = mapped_column(primary_key=True)
    out_path: Mapped[str | None]
    error: Mapped[str | None]
    evaluated_at: Mapped[datetime] = mapped_column(default=utcnow)


class HostStatus(Base):
    __tablename__ = "host_status"

    host: Mapped[str] = mapped_column(primary_key=True)
    last_attempt_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    error: Mapped[str | None]
    current_path: Mapped[str | None]
    boot_path: Mapped[str | None]
    booted_path: Mapped[str | None]
    current_kernel: Mapped[str | None]
    booted_kernel: Mapped[str | None]
    uptime_s: Mapped[float | None]


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    host: Mapped[str]
    mode: Mapped[str]  # test | switch | boot
    sha: Mapped[str]
    # queued | running | interrupted | succeeded | failed
    state: Mapped[str] = mapped_column(default="queued")
    expected_path: Mapped[str | None]
    exit_code: Mapped[int | None]
    error: Mapped[str | None]
    log_path: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class Meta(Base):
    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str | None]


def make_sessionmaker(db_path: Path | str) -> sessionmaker[Session]:
    url = "sqlite://" if str(db_path) == ":memory:" else f"sqlite:///{db_path}"
    # The poller and the deploy worker write from different threads.
    kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}}
    if url == "sqlite://":
        from sqlalchemy.pool import StaticPool

        kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def get_meta(session: Session, key: str) -> str | None:
    row = session.get(Meta, key)
    return row.value if row else None


def set_meta(session: Session, key: str, value: str | None) -> None:
    row = session.get(Meta, key)
    if row:
        row.value = value
    else:
        session.add(Meta(key=key, value=value))


def commit_for_path(session: Session, host: str, path: str | None) -> str | None:
    """Most recent known commit whose evaluation produced `path` for `host`."""
    if not path:
        return None
    stmt = (
        select(Expected.sha)
        .join(Commit, Commit.sha == Expected.sha)
        .where(Expected.host == host, Expected.out_path == path)
        .order_by(Commit.committed_at.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()
