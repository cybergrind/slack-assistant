"""Status service for generating attention-needed items."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from slack_assistant.db.repository import Repository
from slack_assistant.slack.client import SlackClient


logger = logging.getLogger(__name__)


class Priority(Enum):
    """Message priority levels."""

    CRITICAL = 1  # Direct mentions
    HIGH = 2  # DMs
    MEDIUM = 3  # Threads you participated in
    LOW = 4  # Channel messages


@dataclass
class StatusItem:
    """An item requiring attention."""

    priority: Priority
    channel_id: str
    channel_name: str | None
    message_ts: str
    thread_ts: str | None
    user_id: str | None
    user_name: str | None
    text_preview: str
    timestamp: datetime | None
    link: str
    reason: str  # Why this needs attention
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Status:
    """Complete status report."""

    items: list[StatusItem]
    reminders: list[dict[str, Any]]
    generated_at: datetime

    @property
    def by_priority(self) -> dict[Priority, list[StatusItem]]:
        """Group items by priority."""
        result: dict[Priority, list[StatusItem]] = {p: [] for p in Priority}
        for item in self.items:
            result[item.priority].append(item)
        return result


class StatusService:
    """Service for generating status reports."""

    def __init__(self, client: SlackClient, repository: Repository):
        self.client = client
        self.repository = repository

    async def get_status(self, hours_back: int = 24) -> Status:
        """Generate a status report of items needing attention."""
        if not self.client.user_id:
            raise RuntimeError('Client not authenticated')

        since = datetime.now() - timedelta(hours=hours_back)
        items: list[StatusItem] = []

        # Get mentions (highest priority)
        mentions = await self._get_mentions(since)
        items.extend(mentions)

        # Get DMs (high priority)
        dms = await self._get_dms(since)
        items.extend(dms)

        # Get threads you participated in (medium priority)
        threads = await self._get_thread_replies(since)
        items.extend(threads)

        # Get reminders
        reminders = await self._get_reminders()

        # Sort by priority then timestamp
        items.sort(key=lambda x: (x.priority.value, -(x.timestamp.timestamp() if x.timestamp else 0)))

        return Status(
            items=items,
            reminders=reminders,
            generated_at=datetime.now(),
        )

    async def _get_mentions(self, since: datetime) -> list[StatusItem]:
        """Get messages that mention the current user."""
        messages = await self.repository.get_unread_mentions(self.client.user_id, since)
        items = []

        for msg in messages:
            channel = await self.repository.get_channel(msg.channel_id)
            user = await self.repository.get_user(msg.user_id) if msg.user_id else None

            items.append(
                StatusItem(
                    priority=Priority.CRITICAL,
                    channel_id=msg.channel_id,
                    channel_name=channel.name if channel else None,
                    message_ts=msg.ts,
                    thread_ts=msg.thread_ts,
                    user_id=msg.user_id,
                    user_name=user.display_name or user.name if user else None,
                    text_preview=self._truncate(msg.text or '', 100),
                    timestamp=msg.created_at,
                    link=self.client.get_message_link(msg.channel_id, msg.ts, msg.thread_ts),
                    reason='You were mentioned',
                )
            )

        return items

    async def _get_dms(self, since: datetime) -> list[StatusItem]:
        """Get recent DM messages."""
        messages = await self.repository.get_dm_messages(since)
        items = []

        # Filter out messages from self
        messages = [m for m in messages if m.user_id != self.client.user_id]

        for msg in messages:
            channel = await self.repository.get_channel(msg.channel_id)
            user = await self.repository.get_user(msg.user_id) if msg.user_id else None

            items.append(
                StatusItem(
                    priority=Priority.HIGH,
                    channel_id=msg.channel_id,
                    channel_name=channel.name if channel else None,
                    message_ts=msg.ts,
                    thread_ts=msg.thread_ts,
                    user_id=msg.user_id,
                    user_name=user.display_name or user.name if user else None,
                    text_preview=self._truncate(msg.text or '', 100),
                    timestamp=msg.created_at,
                    link=self.client.get_message_link(msg.channel_id, msg.ts, msg.thread_ts),
                    reason='Direct message',
                )
            )

        return items

    async def _get_thread_replies(self, since: datetime) -> list[StatusItem]:
        """Get replies in threads the user participated in."""
        thread_data = await self.repository.get_threads_with_replies(self.client.user_id, since)
        items = []

        seen_threads = set()
        for row in thread_data:
            thread_key = f'{row["channel_id"]}:{row.get("thread_ts") or row["ts"]}'
            if thread_key in seen_threads:
                continue
            seen_threads.add(thread_key)

            user = await self.repository.get_user(row['user_id']) if row.get('user_id') else None

            items.append(
                StatusItem(
                    priority=Priority.MEDIUM,
                    channel_id=row['channel_id'],
                    channel_name=row.get('channel_name'),
                    message_ts=row['ts'],
                    thread_ts=row.get('thread_ts'),
                    user_id=row.get('user_id'),
                    user_name=user.display_name or user.name if user else None,
                    text_preview=self._truncate(row.get('text') or '', 100),
                    timestamp=row.get('created_at'),
                    link=self.client.get_message_link(
                        row['channel_id'],
                        row['ts'],
                        row.get('thread_ts'),
                    ),
                    reason='Reply in thread you participated in',
                )
            )

        return items

    async def _get_reminders(self) -> list[dict[str, Any]]:
        """Get pending reminders (Later section)."""
        reminders = await self.repository.get_pending_reminders(self.client.user_id)
        return [
            {
                'id': r.id,
                'text': r.text,
                'time': r.time.isoformat() if r.time else None,
                'recurring': r.recurring,
            }
            for r in reminders
        ]

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        """Truncate text to max length, adding ellipsis if needed."""
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + '...'
