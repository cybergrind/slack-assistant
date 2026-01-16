"""Slack polling daemon for syncing messages and reactions."""

import asyncio
import logging
from datetime import datetime
from typing import Any

from slack_assistant.config import get_config
from slack_assistant.db.models import Channel, Message, SyncState, User
from slack_assistant.db.repository import Repository
from slack_assistant.slack.client import SlackClient


logger = logging.getLogger(__name__)


class SlackPoller:
    """Background poller that syncs Slack data to the database."""

    def __init__(
        self,
        client: SlackClient,
        repository: Repository,
        poll_interval: int | None = None,
    ):
        self.client = client
        self.repository = repository
        self.poll_interval = poll_interval or get_config().poll_interval_seconds
        self._running = False
        self._channels: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        """Start the polling loop."""
        if not self.client.user_id:
            if not await self.client.authenticate():
                raise RuntimeError('Failed to authenticate with Slack')

        self._running = True
        logger.info(f'Starting poller (interval: {self.poll_interval}s)')

        # Initial sync
        await self._sync_channels()
        await self._sync_all_messages()

        # Main polling loop
        poll_count = 0
        while self._running:
            try:
                await asyncio.sleep(self.poll_interval)
                poll_count += 1
                logger.debug(f'Poll #{poll_count}')

                # Refresh channel list periodically
                if poll_count % 10 == 0:
                    await self._sync_channels()

                await self._sync_all_messages()

            except asyncio.CancelledError:
                logger.info('Poller cancelled')
                break
            except Exception as e:
                logger.exception(f'Error in polling loop: {e}')
                await asyncio.sleep(5)  # Brief pause before retrying

    def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        logger.info('Poller stopping...')

    async def _sync_channels(self) -> None:
        """Sync channel list from Slack."""
        logger.info('Syncing channels...')
        conversations = await self.client.get_conversations()

        for conv in conversations:
            channel = Channel(
                id=conv['id'],
                name=conv.get('name') or conv.get('user'),
                channel_type=self._get_channel_type(conv),
                is_archived=conv.get('is_archived', False),
                created_at=datetime.fromtimestamp(conv['created']) if conv.get('created') else None,
                metadata={k: v for k, v in conv.items() if k not in ('id', 'name', 'is_archived', 'created')},
            )
            await self.repository.upsert_channel(channel)
            self._channels[channel.id] = conv

        logger.info(f'Synced {len(conversations)} channels')

    def _get_channel_type(self, conv: dict[str, Any]) -> str:
        """Determine channel type from conversation data."""
        if conv.get('is_im'):
            return 'im'
        if conv.get('is_mpim'):
            return 'mpim'
        if conv.get('is_private'):
            return 'private_channel'
        return 'public_channel'

    async def _sync_all_messages(self) -> None:
        """Sync messages from all channels."""
        channels = await self.repository.get_all_channels()

        for channel in channels:
            await self._sync_channel_messages(channel)
            # Small delay to avoid rate limits
            await asyncio.sleep(0.2)

    async def _sync_channel_messages(self, channel: Channel) -> None:
        """Sync messages from a single channel."""
        # Get sync state
        sync_state = await self.repository.get_sync_state(channel.id)
        oldest = sync_state.last_ts if sync_state else None

        # Fetch new messages
        messages = await self.client.get_channel_history(channel.id, oldest=oldest)
        if not messages:
            return

        # Messages are returned newest-first
        newest_ts = messages[0].get('ts')
        messages = list(reversed(messages))  # Process oldest first

        new_count = 0
        for msg_data in messages:
            msg = Message.from_slack(channel.id, msg_data)

            # Skip if we've already seen this exact timestamp
            if oldest and msg.ts <= oldest:
                continue

            # Store message
            message_id = await self.repository.upsert_message(msg)

            # Store reactions
            if reactions := msg_data.get('reactions'):
                await self.repository.upsert_reactions(message_id, reactions)

            # Sync thread replies if this is a thread parent
            if msg.reply_count > 0:
                await self._sync_thread_replies(channel.id, msg.ts)

            new_count += 1

            # Cache user info if not seen before
            if msg.user_id:
                await self._ensure_user_cached(msg.user_id)

        if new_count > 0:
            logger.info(f'Synced {new_count} new messages from #{channel.name or channel.id}')

        # Update sync state
        if newest_ts:
            await self.repository.upsert_sync_state(SyncState(channel_id=channel.id, last_ts=newest_ts))

    async def _sync_thread_replies(self, channel_id: str, thread_ts: str) -> None:
        """Sync replies in a thread."""
        replies = await self.client.get_thread_replies(channel_id, thread_ts)

        for reply_data in replies:
            reply = Message.from_slack(channel_id, reply_data)
            message_id = await self.repository.upsert_message(reply)

            if reactions := reply_data.get('reactions'):
                await self.repository.upsert_reactions(message_id, reactions)

            if reply.user_id:
                await self._ensure_user_cached(reply.user_id)

    async def _ensure_user_cached(self, user_id: str) -> None:
        """Ensure user info is cached in the database."""
        existing = await self.repository.get_user(user_id)
        if existing:
            return

        user_info = await self.client.get_user_info(user_id)
        if not user_info:
            return

        user = User(
            id=user_info['id'],
            name=user_info.get('name'),
            real_name=user_info.get('real_name'),
            display_name=user_info.get('profile', {}).get('display_name'),
            is_bot=user_info.get('is_bot', False),
            metadata={k: v for k, v in user_info.items() if k not in ('id', 'name', 'real_name', 'is_bot')},
        )
        await self.repository.upsert_user(user)
