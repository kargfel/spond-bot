import asyncio
import contextlib
from dataclasses import dataclass


@dataclass
class BusEvent:
    type: str
    data: dict


class EventBus:
    def __init__(self):
        self._admin_queues: list[asyncio.Queue] = []
        self._user_queues: dict[str, list[asyncio.Queue]] = {}

    async def publish_admin(self, event_type: str, data: dict) -> None:
        for q in list(self._admin_queues):
            await q.put(BusEvent(type=event_type, data=data))

    async def publish_user(self, user_id: str, event_type: str, data: dict) -> None:
        for q in list(self._user_queues.get(user_id, [])):
            await q.put(BusEvent(type=event_type, data=data))

    def subscribe_admin(self) -> "asyncio.Queue[BusEvent]":
        q: asyncio.Queue[BusEvent] = asyncio.Queue()
        self._admin_queues.append(q)
        return q

    def unsubscribe_admin(self, q: "asyncio.Queue[BusEvent]") -> None:
        with contextlib.suppress(ValueError):
            self._admin_queues.remove(q)

    def subscribe_user(self, user_id: str) -> "asyncio.Queue[BusEvent]":
        q: asyncio.Queue[BusEvent] = asyncio.Queue()
        self._user_queues.setdefault(user_id, []).append(q)
        return q

    def unsubscribe_user(self, user_id: str, q: "asyncio.Queue[BusEvent]") -> None:
        with contextlib.suppress(ValueError):
            self._user_queues.get(user_id, []).remove(q)


# Singleton shared across the application
bus = EventBus()
