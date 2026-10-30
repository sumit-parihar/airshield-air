"""Persist in-app alert notifications and define optional delivery adapters.

Only in-app records are created automatically. Browser/email adapters must be
provided by a configured delivery integration before those channels are used.
"""

from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.models import Alert, NotificationRecord


class NotificationAdapter(Protocol):
    channel: str

    async def deliver(self, alert: Alert) -> None: ...


class BrowserNotificationAdapter(Protocol):
    channel: str  # "browser"

    async def deliver(self, alert: Alert) -> None: ...


class EmailNotificationAdapter(Protocol):
    channel: str  # "email"

    async def deliver(self, alert: Alert) -> None: ...


class NotificationService:
    @staticmethod
    def record_in_app(session: Session, alert: Alert) -> NotificationRecord:
        existing = (
            session.query(NotificationRecord)
            .filter_by(alert_id=alert.id, channel="in_app")
            .one_or_none()
        )
        if existing:
            return existing
        notification = NotificationRecord(alert_id=alert.id, channel="in_app", status="recorded")
        session.add(notification)
        session.flush()
        return notification

    @staticmethod
    async def deliver(
        session_factory: sessionmaker[Session],
        alert_id: int,
        adapter: NotificationAdapter,
    ) -> NotificationRecord:
        if adapter.channel not in ("browser", "email"):
            raise ValueError("External notification adapters must use browser or email channels")
        with session_factory() as session:
            alert = session.get(Alert, alert_id)
            if alert is None:
                raise ValueError("Alert does not exist")
            record = (
                session.query(NotificationRecord)
                .filter_by(alert_id=alert_id, channel=adapter.channel)
                .one_or_none()
            )
            if record is None:
                record = NotificationRecord(alert_id=alert_id, channel=adapter.channel, status="pending")
                session.add(record)
            elif record.status == "delivered":
                return record
            record.status = "pending"
            record.attempted_at = utc_now()
            record.error = None
            session.commit()
            session.expunge(alert)

        try:
            await adapter.deliver(alert)
        except Exception as exc:
            status = "failed"
            safe_error = type(exc).__name__[:240]
        else:
            status = "delivered"
            safe_error = None

        with session_factory() as session:
            with session.begin():
                record = (
                    session.query(NotificationRecord)
                    .filter_by(alert_id=alert_id, channel=adapter.channel)
                    .one()
                )
                record.status = status
                record.error = safe_error
                session.flush()
                session.expunge(record)
                return record


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
