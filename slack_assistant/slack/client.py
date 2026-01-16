"""Slack API client wrapper."""

import logging
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient


logger = logging.getLogger(__name__)


class SlackClient:
    """Async Slack API client wrapper."""

    def __init__(self, token: str):
        self.client = AsyncWebClient(token=token)
        self.user_id: str | None = None
        self.user_name: str | None = None
        self.team_id: str | None = None

    async def authenticate(self) -> bool:
        """Verify token and get current user info."""
        try:
            response = await self.client.auth_test()
            self.user_id = response['user_id']
            self.user_name = response['user']
            self.team_id = response['team_id']
            logger.info(f'Authenticated as {self.user_name} (ID: {self.user_id})')
            return True
        except SlackApiError as e:
            logger.error(f'Authentication failed: {e.response["error"]}')
            return False

    async def get_conversations(self, types: str = 'public_channel,private_channel,mpim,im') -> list[dict[str, Any]]:
        """Fetch all conversations the user is a member of."""
        conversations = []
        cursor = None

        try:
            while True:
                response = await self.client.conversations_list(
                    types=types,
                    exclude_archived=True,
                    limit=200,
                    cursor=cursor,
                )

                for channel in response.get('channels', []):
                    if channel.get('is_member', True):  # DMs don't have is_member
                        conversations.append(channel)

                cursor = response.get('response_metadata', {}).get('next_cursor')
                if not cursor:
                    break

            logger.debug(f'Found {len(conversations)} conversations')
            return conversations

        except SlackApiError as e:
            logger.error(f'Failed to fetch conversations: {e.response["error"]}')
            return []

    async def get_channel_history(
        self,
        channel_id: str,
        oldest: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch messages from a channel."""
        messages = []
        cursor = None

        try:
            while True:
                kwargs: dict[str, Any] = {
                    'channel': channel_id,
                    'limit': min(limit - len(messages), 100),
                }
                if oldest:
                    kwargs['oldest'] = oldest
                if cursor:
                    kwargs['cursor'] = cursor

                response = await self.client.conversations_history(**kwargs)
                messages.extend(response.get('messages', []))

                if len(messages) >= limit:
                    break

                cursor = response.get('response_metadata', {}).get('next_cursor')
                if not cursor:
                    break

            return messages

        except SlackApiError as e:
            error = e.response.get('error', 'unknown')
            if error not in ('channel_not_found', 'not_in_channel'):
                logger.warning(f'Failed to fetch history for {channel_id}: {error}')
            return []

    async def get_thread_replies(
        self,
        channel_id: str,
        thread_ts: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch replies in a thread."""
        try:
            response = await self.client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=limit,
            )
            # First message is the parent, rest are replies
            messages = response.get('messages', [])
            return messages[1:] if len(messages) > 1 else []
        except SlackApiError as e:
            logger.warning(f'Failed to fetch thread {thread_ts}: {e.response["error"]}')
            return []

    async def get_user_info(self, user_id: str) -> dict[str, Any] | None:
        """Get user information."""
        try:
            response = await self.client.users_info(user=user_id)
            return response.get('user')
        except SlackApiError as e:
            logger.warning(f'Failed to get user {user_id}: {e.response["error"]}')
            return None

    async def get_reminders(self) -> list[dict[str, Any]]:
        """Get all reminders for the authenticated user."""
        try:
            response = await self.client.reminders_list()
            return response.get('reminders', [])
        except SlackApiError as e:
            logger.warning(f'Failed to fetch reminders: {e.response["error"]}')
            return []

    async def search_messages(self, query: str, count: int = 20) -> list[dict[str, Any]]:
        """Search messages (requires search:read scope)."""
        try:
            response = await self.client.search_messages(
                query=query,
                count=count,
                sort='timestamp',
                sort_dir='desc',
            )
            return response.get('messages', {}).get('matches', [])
        except SlackApiError as e:
            logger.warning(f'Failed to search messages: {e.response["error"]}')
            return []

    def get_message_link(self, channel_id: str, message_ts: str, thread_ts: str | None = None) -> str:
        """Generate a Slack message permalink."""
        ts_formatted = message_ts.replace('.', '')
        base_url = f'https://slack.com/archives/{channel_id}/p{ts_formatted}'
        if thread_ts and thread_ts != message_ts:
            thread_formatted = thread_ts.replace('.', '')
            base_url += f'?thread_ts={thread_formatted}'
        return base_url
