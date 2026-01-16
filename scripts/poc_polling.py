#!/usr/bin/env python3
"""
POC Polling Script for Slack Assistant

This script validates that user token polling can capture all messages and reactions.
Run this for a few minutes while sending test messages in Slack to verify functionality.

Usage:
    export SLACK_USER_TOKEN=xoxp-...
    python scripts/poc_polling.py

Requirements:
    pip install slack-sdk
"""

import asyncio
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


class SlackPoller:
    """Polls Slack conversations for new messages and reactions."""

    def __init__(self, token: str, poll_interval: int = 60):
        self.client = AsyncWebClient(token=token)
        self.poll_interval = poll_interval
        # Track last seen timestamp per channel to avoid duplicates
        self.channel_cursors: dict[str, str] = {}
        # Track seen message timestamps to detect new reactions
        self.seen_messages: dict[str, dict[str, Any]] = defaultdict(dict)
        self.user_id: str | None = None
        self.user_name: str | None = None
        # Cache for user ID -> display name mapping
        self.users_cache: dict[str, str] = {}

    async def authenticate(self) -> bool:
        """Verify token and get current user info."""
        try:
            response = await self.client.auth_test()
            self.user_id = response['user_id']
            self.user_name = response['user']
            logger.info(f'Authenticated as {self.user_name} (ID: {self.user_id})')
            return True
        except SlackApiError as e:
            logger.error(f'Authentication failed: {e.response["error"]}')
            return False

    async def get_all_conversations(self) -> list[dict]:
        """Fetch all conversations the user is a member of."""
        conversations = []
        cursor = None

        # Types: public_channel, private_channel, mpim (group DM), im (DM)
        types = 'public_channel,private_channel,mpim,im'

        try:
            while True:
                response = await self.client.conversations_list(
                    types=types,
                    exclude_archived=True,
                    limit=200,
                    cursor=cursor,
                )

                for channel in response.get('channels', []):
                    # Only include channels user is member of
                    if channel.get('is_member', True):  # DMs don't have is_member
                        conversations.append(channel)

                cursor = response.get('response_metadata', {}).get('next_cursor')
                if not cursor:
                    break

            logger.info(f'Found {len(conversations)} conversations')
            return conversations

        except SlackApiError as e:
            logger.error(f'Failed to fetch conversations: {e.response["error"]}')
            return []

    async def get_channel_history(self, channel_id: str, oldest: str | None = None) -> list[dict]:
        """Fetch messages from a channel since the given timestamp."""
        messages = []

        try:
            kwargs = {
                'channel': channel_id,
                'limit': 100,
            }
            if oldest:
                kwargs['oldest'] = oldest

            response = await self.client.conversations_history(**kwargs)
            messages = response.get('messages', [])

            # Note: We're not paginating here for POC simplicity
            # In production, we'd handle cursor pagination

        except SlackApiError as e:
            error = e.response.get('error', 'unknown')
            # Some channels may not be accessible
            if error not in ('channel_not_found', 'not_in_channel'):
                logger.warning(f'Failed to fetch history for {channel_id}: {error}')

        return messages

    async def get_thread_replies(self, channel_id: str, thread_ts: str) -> list[dict]:
        """Fetch replies in a thread."""
        try:
            response = await self.client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=100,
            )
            # First message is the parent, rest are replies
            return response.get('messages', [])[1:]
        except SlackApiError as e:
            logger.warning(f'Failed to fetch thread {thread_ts}: {e.response["error"]}')
            return []

    async def get_user_name(self, user_id: str) -> str:
        """Get user display name, with caching."""
        if not user_id:
            return 'unknown'

        if user_id in self.users_cache:
            return self.users_cache[user_id]

        try:
            response = await self.client.users_info(user=user_id)
            user = response.get('user', {})
            # Prefer display_name, fall back to real_name, then name
            name = (
                user.get('profile', {}).get('display_name')
                or user.get('real_name')
                or user.get('name')
                or user_id
            )
            self.users_cache[user_id] = name
            return name
        except SlackApiError as e:
            logger.debug(f'Failed to get user info for {user_id}: {e.response["error"]}')
            self.users_cache[user_id] = user_id
            return user_id

    async def get_user_names(self, user_ids: list[str]) -> list[str]:
        """Get display names for multiple users."""
        return [await self.get_user_name(uid) for uid in user_ids]

    async def format_message(self, msg: dict, channel_name: str) -> str:
        """Format a message for logging."""
        ts = msg.get('ts', '')
        user_id = msg.get('user', 'unknown')
        user_name = await self.get_user_name(user_id)
        text = msg.get('text', '')[:100]  # Truncate for readability

        # Format timestamp
        try:
            dt = datetime.fromtimestamp(float(ts))
            time_str = dt.strftime('%H:%M:%S')
        except (ValueError, TypeError):
            time_str = ts

        # Check for thread
        thread_ts = msg.get('thread_ts')
        thread_indicator = ' [thread]' if thread_ts and thread_ts != ts else ''

        return f'[{time_str}] #{channel_name} <{user_name}>{thread_indicator}: {text}'

    async def format_reactions(self, msg: dict, channel_name: str) -> list[str]:
        """Format reactions for logging."""
        reactions = msg.get('reactions', [])
        if not reactions:
            return []

        lines = []
        for reaction in reactions:
            name = reaction.get('name', '')
            count = reaction.get('count', 0)
            user_ids = reaction.get('users', [])
            user_names = await self.get_user_names(user_ids)
            lines.append(f'  Reaction :{name}: x{count} from {user_names}')

        return lines

    async def poll_once(self, channels: list[dict]) -> None:
        """Poll all channels once and log new messages/reactions."""
        for channel in channels:
            channel_id = channel['id']
            channel_name = channel.get('name') or channel.get('user') or channel_id
            oldest = self.channel_cursors.get(channel_id)

            messages = await self.get_channel_history(channel_id, oldest)

            if not messages:
                continue

            # Messages are returned newest-first, reverse for chronological logging
            messages = list(reversed(messages))

            for msg in messages:
                msg_ts = msg.get('ts', '')
                msg_key = f'{channel_id}:{msg_ts}'

                # Check if this is a new message
                is_new = msg_key not in self.seen_messages

                if is_new:
                    formatted_msg = await self.format_message(msg, channel_name)
                    logger.info(f'NEW: {formatted_msg}')

                    # Check for thread replies
                    if msg.get('reply_count', 0) > 0:
                        replies = await self.get_thread_replies(channel_id, msg_ts)
                        for reply in replies:
                            reply_key = f'{channel_id}:{reply.get("ts", "")}'
                            if reply_key not in self.seen_messages:
                                formatted_reply = await self.format_message(reply, channel_name)
                                logger.info(f'  REPLY: {formatted_reply}')
                                self.seen_messages[reply_key] = reply

                # Check for new reactions
                old_reactions = self.seen_messages.get(msg_key, {}).get('reactions', [])
                new_reactions = msg.get('reactions', [])

                if new_reactions != old_reactions:
                    formatted_msg = await self.format_message(msg, channel_name)
                    reaction_lines = await self.format_reactions(msg, channel_name)
                    for line in reaction_lines:
                        logger.info(f'REACTION: {formatted_msg}\n{line}')

                self.seen_messages[msg_key] = msg

            # Update cursor to the newest message timestamp
            if messages:
                newest_ts = messages[-1].get('ts', '')
                if newest_ts:
                    self.channel_cursors[channel_id] = newest_ts

            # Small delay to avoid rate limits
            await asyncio.sleep(0.1)

    async def run(self) -> None:
        """Main polling loop."""
        if not await self.authenticate():
            logger.error('Failed to authenticate. Check your SLACK_USER_TOKEN.')
            return

        logger.info(f'Starting polling loop (interval: {self.poll_interval}s)')
        logger.info('Send messages in Slack to see them appear here...')
        logger.info('Press Ctrl+C to stop\n')

        # Get initial channel list
        channels = await self.get_all_conversations()
        if not channels:
            logger.error('No conversations found. Check token scopes.')
            return

        # Initial poll to establish baselines
        logger.info('Performing initial sync...')
        await self.poll_once(channels)
        logger.info(f'Initial sync complete. Tracking {len(self.seen_messages)} messages.\n')

        # Main polling loop
        poll_count = 0
        while True:
            try:
                await asyncio.sleep(self.poll_interval)
                poll_count += 1
                logger.debug(f'Poll #{poll_count}')

                # Refresh channel list periodically
                if poll_count % 10 == 0:
                    channels = await self.get_all_conversations()

                await self.poll_once(channels)

            except asyncio.CancelledError:
                logger.info('Polling stopped.')
                break


async def main():
    token = os.environ.get('SLACK_USER_TOKEN')
    if not token:
        logger.error('SLACK_USER_TOKEN environment variable not set')
        logger.error('Get a user token from https://api.slack.com/apps')
        sys.exit(1)

    if not token.startswith('xoxp-'):
        logger.warning('Token does not start with xoxp-. Expected user OAuth token.')

    poll_interval = int(os.environ.get('POLL_INTERVAL_SECONDS', '60'))

    poller = SlackPoller(token, poll_interval)

    try:
        await poller.run()
    except KeyboardInterrupt:
        logger.info('Interrupted by user')


if __name__ == '__main__':
    asyncio.run(main())
