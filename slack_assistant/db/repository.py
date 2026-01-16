"""Database repository for CRUD operations."""

import json
from datetime import datetime
from typing import Any

import asyncpg

from slack_assistant.db.connection import get_connection
from slack_assistant.db.models import Channel, Message, Reaction, Reminder, SyncState, User


class Repository:
    """Database repository for Slack Assistant."""

    # Channel operations

    async def upsert_channel(self, channel: Channel) -> None:
        """Insert or update a channel."""
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO channels (id, name, channel_type, is_archived, created_at, updated_at, metadata)
                VALUES ($1, $2, $3, $4, $5, NOW(), $6)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    channel_type = EXCLUDED.channel_type,
                    is_archived = EXCLUDED.is_archived,
                    updated_at = NOW(),
                    metadata = EXCLUDED.metadata
                """,
                channel.id,
                channel.name,
                channel.channel_type,
                channel.is_archived,
                channel.created_at,
                json.dumps(channel.metadata),
            )

    async def get_channel(self, channel_id: str) -> Channel | None:
        """Get a channel by ID."""
        async with get_connection() as conn:
            row = await conn.fetchrow('SELECT * FROM channels WHERE id = $1', channel_id)
            if row:
                return Channel(
                    id=row['id'],
                    name=row['name'],
                    channel_type=row['channel_type'],
                    is_archived=row['is_archived'],
                    created_at=row['created_at'],
                    updated_at=row['updated_at'],
                    metadata=json.loads(row['metadata']) if row['metadata'] else {},
                )
            return None

    async def get_all_channels(self) -> list[Channel]:
        """Get all channels."""
        async with get_connection() as conn:
            rows = await conn.fetch('SELECT * FROM channels WHERE is_archived = FALSE')
            return [
                Channel(
                    id=row['id'],
                    name=row['name'],
                    channel_type=row['channel_type'],
                    is_archived=row['is_archived'],
                    created_at=row['created_at'],
                    updated_at=row['updated_at'],
                    metadata=json.loads(row['metadata']) if row['metadata'] else {},
                )
                for row in rows
            ]

    # User operations

    async def upsert_user(self, user: User) -> None:
        """Insert or update a user."""
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO users (id, name, real_name, display_name, is_bot, updated_at, metadata)
                VALUES ($1, $2, $3, $4, $5, NOW(), $6)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    real_name = EXCLUDED.real_name,
                    display_name = EXCLUDED.display_name,
                    is_bot = EXCLUDED.is_bot,
                    updated_at = NOW(),
                    metadata = EXCLUDED.metadata
                """,
                user.id,
                user.name,
                user.real_name,
                user.display_name,
                user.is_bot,
                json.dumps(user.metadata),
            )

    async def get_user(self, user_id: str) -> User | None:
        """Get a user by ID."""
        async with get_connection() as conn:
            row = await conn.fetchrow('SELECT * FROM users WHERE id = $1', user_id)
            if row:
                return User(
                    id=row['id'],
                    name=row['name'],
                    real_name=row['real_name'],
                    display_name=row['display_name'],
                    is_bot=row['is_bot'],
                    updated_at=row['updated_at'],
                    metadata=json.loads(row['metadata']) if row['metadata'] else {},
                )
            return None

    # Message operations

    async def upsert_message(self, message: Message) -> int:
        """Insert or update a message, returning the database ID."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO messages (
                    channel_id, ts, user_id, text, thread_ts, reply_count,
                    is_edited, message_type, created_at, updated_at, metadata
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), $10)
                ON CONFLICT (channel_id, ts) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    text = EXCLUDED.text,
                    thread_ts = EXCLUDED.thread_ts,
                    reply_count = EXCLUDED.reply_count,
                    is_edited = EXCLUDED.is_edited,
                    updated_at = NOW(),
                    metadata = EXCLUDED.metadata
                RETURNING id
                """,
                message.channel_id,
                message.ts,
                message.user_id,
                message.text,
                message.thread_ts,
                message.reply_count,
                message.is_edited,
                message.message_type,
                message.created_at,
                json.dumps(message.metadata),
            )
            return row['id']

    async def get_message(self, channel_id: str, ts: str) -> Message | None:
        """Get a message by channel and timestamp."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                'SELECT * FROM messages WHERE channel_id = $1 AND ts = $2',
                channel_id,
                ts,
            )
            if row:
                return self._row_to_message(row)
            return None

    async def get_message_by_id(self, message_id: int) -> Message | None:
        """Get a message by database ID."""
        async with get_connection() as conn:
            row = await conn.fetchrow('SELECT * FROM messages WHERE id = $1', message_id)
            if row:
                return self._row_to_message(row)
            return None

    async def get_messages_since(
        self,
        channel_id: str,
        since_ts: str | None = None,
        limit: int = 100,
    ) -> list[Message]:
        """Get messages from a channel since a timestamp."""
        async with get_connection() as conn:
            if since_ts:
                rows = await conn.fetch(
                    """
                    SELECT * FROM messages
                    WHERE channel_id = $1 AND ts > $2
                    ORDER BY ts ASC
                    LIMIT $3
                    """,
                    channel_id,
                    since_ts,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM messages
                    WHERE channel_id = $1
                    ORDER BY ts DESC
                    LIMIT $2
                    """,
                    channel_id,
                    limit,
                )
            return [self._row_to_message(row) for row in rows]

    async def get_thread_messages(self, channel_id: str, thread_ts: str) -> list[Message]:
        """Get all messages in a thread."""
        async with get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM messages
                WHERE channel_id = $1 AND (ts = $2 OR thread_ts = $2)
                ORDER BY ts ASC
                """,
                channel_id,
                thread_ts,
            )
            return [self._row_to_message(row) for row in rows]

    def _row_to_message(self, row: asyncpg.Record) -> Message:
        """Convert a database row to a Message object."""
        return Message(
            id=row['id'],
            channel_id=row['channel_id'],
            ts=row['ts'],
            user_id=row['user_id'],
            text=row['text'],
            thread_ts=row['thread_ts'],
            reply_count=row['reply_count'],
            is_edited=row['is_edited'],
            message_type=row['message_type'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            metadata=json.loads(row['metadata']) if row['metadata'] else {},
        )

    # Reaction operations

    async def upsert_reactions(self, message_id: int, reactions: list[dict[str, Any]]) -> None:
        """Update reactions for a message (replace all)."""
        async with get_connection() as conn:
            # Delete existing reactions
            await conn.execute('DELETE FROM reactions WHERE message_id = $1', message_id)

            # Insert new reactions
            for reaction in reactions:
                name = reaction.get('name', '')
                users = reaction.get('users', [])
                for user_id in users:
                    await conn.execute(
                        """
                        INSERT INTO reactions (message_id, name, user_id, created_at)
                        VALUES ($1, $2, $3, NOW())
                        ON CONFLICT DO NOTHING
                        """,
                        message_id,
                        name,
                        user_id,
                    )

    async def get_reactions(self, message_id: int) -> list[Reaction]:
        """Get reactions for a message."""
        async with get_connection() as conn:
            rows = await conn.fetch(
                'SELECT * FROM reactions WHERE message_id = $1',
                message_id,
            )
            return [
                Reaction(
                    id=row['id'],
                    message_id=row['message_id'],
                    name=row['name'],
                    user_id=row['user_id'],
                    created_at=row['created_at'],
                )
                for row in rows
            ]

    # Sync state operations

    async def get_sync_state(self, channel_id: str) -> SyncState | None:
        """Get sync state for a channel."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                'SELECT * FROM sync_state WHERE channel_id = $1',
                channel_id,
            )
            if row:
                return SyncState(
                    channel_id=row['channel_id'],
                    last_ts=row['last_ts'],
                    last_sync_at=row['last_sync_at'],
                )
            return None

    async def upsert_sync_state(self, sync_state: SyncState) -> None:
        """Update sync state for a channel."""
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO sync_state (channel_id, last_ts, last_sync_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (channel_id) DO UPDATE SET
                    last_ts = EXCLUDED.last_ts,
                    last_sync_at = NOW()
                """,
                sync_state.channel_id,
                sync_state.last_ts,
            )

    # Reminder operations

    async def upsert_reminder(self, reminder: Reminder) -> None:
        """Insert or update a reminder."""
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO reminders (
                    id, user_id, text, time, complete_ts, recurring,
                    created_at, updated_at, metadata
                )
                VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW(), $7)
                ON CONFLICT (id) DO UPDATE SET
                    text = EXCLUDED.text,
                    time = EXCLUDED.time,
                    complete_ts = EXCLUDED.complete_ts,
                    recurring = EXCLUDED.recurring,
                    updated_at = NOW(),
                    metadata = EXCLUDED.metadata
                """,
                reminder.id,
                reminder.user_id,
                reminder.text,
                reminder.time,
                reminder.complete_ts,
                reminder.recurring,
                json.dumps(reminder.metadata),
            )

    async def get_pending_reminders(self, user_id: str) -> list[Reminder]:
        """Get pending (incomplete) reminders for a user."""
        async with get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM reminders
                WHERE user_id = $1 AND complete_ts IS NULL
                ORDER BY time ASC
                """,
                user_id,
            )
            return [
                Reminder(
                    id=row['id'],
                    user_id=row['user_id'],
                    text=row['text'],
                    time=row['time'],
                    complete_ts=row['complete_ts'],
                    recurring=row['recurring'],
                    created_at=row['created_at'],
                    updated_at=row['updated_at'],
                    metadata=json.loads(row['metadata']) if row['metadata'] else {},
                )
                for row in rows
            ]

    # Status queries

    async def get_unread_mentions(self, user_id: str, since: datetime | None = None) -> list[Message]:
        """Get messages that mention a user."""
        async with get_connection() as conn:
            query = """
                SELECT m.* FROM messages m
                WHERE m.text LIKE $1
            """
            params: list[Any] = [f'%<@{user_id}>%']

            if since:
                query += ' AND m.created_at > $2'
                params.append(since)

            query += ' ORDER BY m.created_at DESC LIMIT 50'

            rows = await conn.fetch(query, *params)
            return [self._row_to_message(row) for row in rows]

    async def get_dm_messages(self, since: datetime | None = None) -> list[Message]:
        """Get recent DM messages."""
        async with get_connection() as conn:
            query = """
                SELECT m.* FROM messages m
                JOIN channels c ON m.channel_id = c.id
                WHERE c.channel_type = 'im'
            """
            params: list[Any] = []

            if since:
                query += ' AND m.created_at > $1'
                params.append(since)

            query += ' ORDER BY m.created_at DESC LIMIT 50'

            rows = await conn.fetch(query, *params)
            return [self._row_to_message(row) for row in rows]

    async def get_threads_with_replies(self, user_id: str, since: datetime | None = None) -> list[dict[str, Any]]:
        """Get threads where user participated that have new replies."""
        async with get_connection() as conn:
            query = """
                WITH user_threads AS (
                    SELECT DISTINCT channel_id, COALESCE(thread_ts, ts) as thread_ts
                    FROM messages
                    WHERE user_id = $1
                )
                SELECT m.*, c.name as channel_name
                FROM messages m
                JOIN user_threads ut ON m.channel_id = ut.channel_id
                    AND (m.ts = ut.thread_ts OR m.thread_ts = ut.thread_ts)
                JOIN channels c ON m.channel_id = c.id
                WHERE m.user_id != $1
            """
            params: list[Any] = [user_id]

            if since:
                query += ' AND m.created_at > $2'
                params.append(since)

            query += ' ORDER BY m.created_at DESC LIMIT 100'

            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]
