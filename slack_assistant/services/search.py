"""Search service for finding relevant messages."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from slack_assistant.db.connection import get_connection
from slack_assistant.db.models import Message
from slack_assistant.db.repository import Repository
from slack_assistant.services.embeddings import EmbeddingService
from slack_assistant.slack.client import SlackClient


logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A search result with relevance score."""

    message: Message
    channel_name: str | None
    user_name: str | None
    score: float
    link: str
    match_type: str  # 'vector', 'text', 'slack_api'


class SearchService:
    """Service for searching messages."""

    def __init__(
        self,
        client: SlackClient,
        repository: Repository,
        embedding_service: EmbeddingService | None = None,
    ):
        self.client = client
        self.repository = repository
        self.embedding_service = embedding_service

    async def search(
        self,
        query: str,
        limit: int = 20,
        use_vector: bool = True,
        use_text: bool = True,
        use_slack_api: bool = False,
    ) -> list[SearchResult]:
        """Search for messages matching the query.

        Args:
            query: Search query
            limit: Maximum number of results
            use_vector: Use vector similarity search (requires embeddings)
            use_text: Use text-based search
            use_slack_api: Use Slack's search API (requires search:read scope)

        Returns:
            List of search results sorted by relevance
        """
        results: list[SearchResult] = []

        # Vector search
        if use_vector and self.embedding_service:
            vector_results = await self._vector_search(query, limit)
            results.extend(vector_results)

        # Text search
        if use_text:
            text_results = await self._text_search(query, limit)
            results.extend(text_results)

        # Slack API search
        if use_slack_api:
            api_results = await self._slack_api_search(query, limit)
            results.extend(api_results)

        # Deduplicate and sort by score
        seen = set()
        unique_results = []
        for result in sorted(results, key=lambda r: r.score, reverse=True):
            key = f'{result.message.channel_id}:{result.message.ts}'
            if key not in seen:
                seen.add(key)
                unique_results.append(result)

        return unique_results[:limit]

    async def _vector_search(self, query: str, limit: int) -> list[SearchResult]:
        """Search using vector similarity."""
        if not self.embedding_service:
            return []

        # Generate embedding for query
        query_embedding = await self.embedding_service.generate_embedding(query)
        if query_embedding is None:
            logger.warning('Could not generate query embedding')
            return []

        async with get_connection() as conn:
            # Use cosine similarity for vector search
            rows = await conn.fetch(
                """
                SELECT
                    m.*,
                    c.name as channel_name,
                    u.display_name as user_name,
                    1 - (me.embedding <=> $1::vector) as similarity
                FROM message_embeddings me
                JOIN messages m ON me.message_id = m.id
                LEFT JOIN channels c ON m.channel_id = c.id
                LEFT JOIN users u ON m.user_id = u.id
                ORDER BY me.embedding <=> $1::vector
                LIMIT $2
                """,
                query_embedding,
                limit,
            )

        results = []
        for row in rows:
            message = self._row_to_message(row)
            results.append(
                SearchResult(
                    message=message,
                    channel_name=row['channel_name'],
                    user_name=row['user_name'],
                    score=float(row['similarity']),
                    link=self.client.get_message_link(message.channel_id, message.ts, message.thread_ts),
                    match_type='vector',
                )
            )

        return results

    async def _text_search(self, query: str, limit: int) -> list[SearchResult]:
        """Search using text matching."""
        # Simple ILIKE search - could be improved with full-text search
        search_pattern = f'%{query}%'

        async with get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    m.*,
                    c.name as channel_name,
                    u.display_name as user_name
                FROM messages m
                LEFT JOIN channels c ON m.channel_id = c.id
                LEFT JOIN users u ON m.user_id = u.id
                WHERE m.text ILIKE $1
                ORDER BY m.created_at DESC
                LIMIT $2
                """,
                search_pattern,
                limit,
            )

        results = []
        for row in rows:
            message = self._row_to_message(row)
            # Simple relevance score based on match position
            text = message.text or ''
            match = re.search(re.escape(query), text, re.IGNORECASE)
            score = 1.0 - (match.start() / len(text)) if match and text else 0.5

            results.append(
                SearchResult(
                    message=message,
                    channel_name=row['channel_name'],
                    user_name=row['user_name'],
                    score=score,
                    link=self.client.get_message_link(message.channel_id, message.ts, message.thread_ts),
                    match_type='text',
                )
            )

        return results

    async def _slack_api_search(self, query: str, limit: int) -> list[SearchResult]:
        """Search using Slack's search API."""
        matches = await self.client.search_messages(query, count=limit)

        results = []
        for match in matches:
            channel_id = match.get('channel', {}).get('id', '')
            ts = match.get('ts', '')

            message = Message(
                id=None,
                channel_id=channel_id,
                ts=ts,
                user_id=match.get('user'),
                text=match.get('text'),
                thread_ts=match.get('thread_ts'),
                created_at=datetime.fromtimestamp(float(ts)) if ts else None,
            )

            results.append(
                SearchResult(
                    message=message,
                    channel_name=match.get('channel', {}).get('name'),
                    user_name=match.get('username'),
                    score=float(match.get('score', 0.5)),
                    link=match.get('permalink', self.client.get_message_link(channel_id, ts)),
                    match_type='slack_api',
                )
            )

        return results

    async def find_context(self, message_link: str, limit: int = 10) -> list[SearchResult]:
        """Find related messages given a Slack message link.

        Args:
            message_link: Slack message permalink
            limit: Maximum number of related messages to return

        Returns:
            List of related messages
        """
        # Parse message link to extract channel_id and ts
        # Format: https://workspace.slack.com/archives/CHANNEL_ID/pTIMESTAMP
        # or: slack://channel?id=CHANNEL_ID&message=TIMESTAMP
        import urllib.parse

        parsed = urllib.parse.urlparse(message_link)

        channel_id = None
        message_ts = None

        if 'slack.com' in parsed.netloc or parsed.path.startswith('/archives/'):
            # Web URL format
            parts = parsed.path.strip('/').split('/')
            if len(parts) >= 2 and parts[0] == 'archives':
                channel_id = parts[1]
                if len(parts) >= 3:
                    # Convert pTIMESTAMP to TIMESTAMP.XXXXXX format
                    ts_part = parts[2]
                    if ts_part.startswith('p'):
                        ts_digits = ts_part[1:]
                        message_ts = f'{ts_digits[:-6]}.{ts_digits[-6:]}'
        elif parsed.scheme == 'slack':
            # Slack URL scheme
            params = urllib.parse.parse_qs(parsed.query)
            channel_id = params.get('id', [None])[0]
            message_ts = params.get('message', [None])[0]

        if not channel_id or not message_ts:
            logger.warning(f'Could not parse message link: {message_link}')
            return []

        # Get the source message
        source_message = await self.repository.get_message(channel_id, message_ts)
        if not source_message:
            logger.warning(f'Message not found: {channel_id}/{message_ts}')
            return []

        # Search for related messages using the source message text
        if source_message.text:
            return await self.search(source_message.text, limit=limit, use_slack_api=False)

        return []

    def _row_to_message(self, row: Any) -> Message:
        """Convert a database row to a Message object."""
        import json

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
