"""
Reminders: "registration opens in 8 / 4 / 1 hours and you have not chosen yet".

SpondBot only answers events the member has decided. An event left on "Leave to me" is
answered by nobody, so shortly before registration opens each such member gets a push
notification, once per threshold:

  - "Undecided" means what the dashboard's inbox means: answer "Leave to me", not answered yet,
    registration opening still ahead, event not over. Deciding at any time ends the reminders.
  - A job looks every minute. For an event it takes the smallest threshold the remaining time
    has fallen under (7 h left: the 8 h reminder; 3 h left: the 4 h one), so an event found late
    gets one reminder, not three at once.
  - A reminder is claimed by inserting a reminder_log row (primary key event + hours) before it
    is sent, so a restart or an overlapping run can never send it twice.
  - Whether a member gets it follows their notification settings (reminder_8h / _4h / _1h).
"""
import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.event import CHOICE_MANUAL, STATUS_PENDING, Event
from app.models.reminder_log import ReminderLog
from app.models.user import User
from app.services import audit, push

logger = logging.getLogger(__name__)

THRESHOLD_HOURS = (8, 4, 1)


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; treat them as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def due_threshold(remaining: timedelta) -> int | None:
    """The smallest threshold (in hours) the remaining time has fallen under; None if more than 8 h or already past."""
    if remaining <= timedelta(0):
        return None
    for hours in sorted(THRESHOLD_HOURS):
        if remaining <= timedelta(hours=hours):
            return hours
    return None


def humanize_remaining(remaining: timedelta) -> str:
    """"8 hours", "1 hour", "35 minutes": rounded, so a reminder sent at 59 minutes reads "1 hour"."""
    minutes = max(1, math.floor(remaining.total_seconds() / 60 + 0.5))
    if minutes >= 90:
        hours = math.floor(minutes / 60 + 0.5)
        return f"{hours} hours"
    if minutes >= 50:
        return "1 hour"
    return f"{minutes} minute" + ("" if minutes == 1 else "s")


def build_reminder_payload(event_id, heading: str | None, remaining: timedelta) -> dict:
    name = (heading or "An event").strip()[:120]
    return {
        "title": f"Registration opens in {humanize_remaining(remaining)}",
        "body": f"{name}: you haven't chosen yet. Pick Going or Not going and SpondBot answers for you.",
        "tag": f"reminder-{event_id}",  # a newer reminder for the same event replaces the older one
        "url": "/dashboard",
        "outcome": "success",
    }


async def find_due(db: AsyncSession, now: datetime) -> list[tuple[Event, int, timedelta]]:
    """Undecided events that crossed a threshold and have no reminder for it yet: (event, hours, remaining)."""
    horizon = now + timedelta(hours=max(THRESHOLD_HOURS))
    events = (
        await db.execute(
            select(Event)
            .join(User, User.id == Event.user_id)
            .where(
                User.is_active.is_(True),
                Event.user_choice == CHOICE_MANUAL,
                Event.status == STATUS_PENDING,
                Event.invite_time.is_not(None),
                Event.invite_time > now,
                Event.invite_time <= horizon,
                or_(Event.start_timestamp.is_(None), Event.start_timestamp > now),
            )
        )
    ).scalars().all()

    candidates = []
    for event in events:
        remaining = _aware(event.invite_time) - now
        hours = due_threshold(remaining)
        if hours is not None:
            candidates.append((event, hours, remaining))
    if not candidates:
        return []
    already = set(
        (await db.execute(select(ReminderLog.event_id, ReminderLog.hours).where(ReminderLog.event_id.in_([e.id for e, _, _ in candidates]))))
        .all()
    )
    return [(e, h, r) for e, h, r in candidates if (e.id, h) not in already]


async def _claim(db: AsyncSession, event_id, hours: int, now: datetime) -> bool:
    """Insert the reminder_log row. False when someone else already did: then nothing is sent."""
    db.add(ReminderLog(event_id=event_id, hours=hours, sent_at=now))
    try:
        await db.commit()
        return True
    except IntegrityError:
        await db.rollback()
        return False


async def run_reminders(now: datetime | None = None) -> int:
    """Scheduler entry point (every minute). Returns how many reminders were sent. Never raises."""
    if not push.push_enabled():
        return 0
    now = now or datetime.now(timezone.utc)
    sent = 0
    try:
        async with AsyncSessionLocal() as db:
            for event, hours, remaining in await find_due(db, now):
                try:
                    if not await _claim(db, event.id, hours, now):
                        continue
                    devices, delivered = await push.send_to_spond_user(
                        db, event.user_id, build_reminder_payload(event.id, event.heading, remaining), kind=f"reminder_{hours}h"
                    )
                    if devices:  # members without devices or with this reminder off leave no trace
                        sent += 1
                        member = await db.get(User, event.user_id)
                        await audit.record_system(
                            "reminder.sent", target_type="event", target_id=event.id, target_label=event.heading,
                            outcome="success" if delivered else "failed",
                            details={"hours": hours, "member": member.display_name if member else None,
                                     "devices": devices, "delivered": delivered},
                        )
                except Exception:
                    logger.exception("Reminder for event %s (%dh) failed.", event.id, hours)
    except Exception:
        logger.exception("Reminder run failed.")
    return sent
