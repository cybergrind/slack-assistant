"""Database models for Slack Assistant.

These are dataclasses representing database rows, not ORM models.
We use raw asyncpg for better async performance.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Channel:
    """Slack channel/conversation."""

    id: str
    name: str | None
    channel_type: str  # public_channel, private_channel, mpim, im
    is_archived: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class User:
    """Slack user cache."""

    id: str
    name: str | None = None
    real_name: str | None = None
    display_name: str | None = None
    is_bot: bool = False
    updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    """Slack message."""

    id: int | None  # Database ID, None for new messages
    channel_id: str
    ts: str  # Slack timestamp
    user_id: str | None = None
    text: str | None = None
    thread_ts: str | None = None
    reply_count: int = 0
    is_edited: bool = False
    message_type: str = 'message'
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_thread_reply(self) -> bool:
        """Check if message is a reply in a thread."""
        return self.thread_ts is not None and self.thread_ts != self.ts

    @property
    def is_thread_parent(self) -> bool:
        """Check if message is the parent of a thread."""
        return self.reply_count > 0

    @classmethod
    def from_slack(cls, channel_id: str, msg: dict[str, Any]) -> 'Message':
        """Create Message from Slack API response."""
        ts = msg.get('ts', '')
        created_at = None
        if ts:
            try:
                created_at = datetime.fromtimestamp(float(ts))
            except (ValueError, TypeError):
                pass

        return cls(
            id=None,
            channel_id=channel_id,
            ts=ts,
            user_id=msg.get('user'),
            text=msg.get('text'),
            thread_ts=msg.get('thread_ts'),
            reply_count=msg.get('reply_count', 0),
            is_edited='edited' in msg,
            message_type=msg.get('type', 'message'),
            created_at=created_at,
            metadata={
                k: v
                for k, v in msg.items()
                if k not in ('ts', 'user', 'text', 'thread_ts', 'reply_count', 'type', 'edited')
            },
        )


@dataclass
class Reaction:
    """Reaction on a message."""

    id: int | None
    message_id: int
    name: str  # Emoji name without colons
    user_id: str
    created_at: datetime | None = None


@dataclass
class MessageEmbedding:
    """Vector embedding for a message."""

    id: int | None
    message_id: int
    embedding: list[float] | None = None
    model: str = 'text-embedding-ada-002'
    created_at: datetime | None = None


@dataclass
class SyncState:
    """Sync state for a channel."""

    channel_id: str
    last_ts: str | None = None
    last_sync_at: datetime | None = None


@dataclass
class Reminder:
    """Slack reminder."""

    id: str
    user_id: str
    text: str | None = None
    time: datetime | None = None
    complete_ts: datetime | None = None
    recurring: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
