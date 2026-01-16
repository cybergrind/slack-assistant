"""Embedding generation service for vector search."""

import logging
from typing import Any

from slack_assistant.config import get_config
from slack_assistant.db.connection import get_connection
from slack_assistant.db.repository import Repository


logger = logging.getLogger(__name__)


class EmbeddingService:
    """Service for generating and storing message embeddings."""

    def __init__(self, repository: Repository, api_key: str | None = None):
        self.repository = repository
        self.api_key = api_key
        self.model = get_config().embedding_model

    async def generate_embedding(self, text: str) -> list[float] | None:
        """Generate embedding for text using configured model.

        This is a placeholder that returns None.
        In production, you would integrate with:
        - OpenAI's text-embedding-ada-002
        - Local models like sentence-transformers
        - Anthropic's embeddings (when available)
        """
        if not text or not text.strip():
            return None

        # TODO: Implement actual embedding generation
        # Example with OpenAI:
        # import openai
        # response = await openai.Embedding.acreate(
        #     input=text,
        #     model=self.model
        # )
        # return response['data'][0]['embedding']

        logger.warning('Embedding generation not implemented - returning None')
        return None

    async def embed_message(self, message_id: int, text: str) -> bool:
        """Generate and store embedding for a message."""
        embedding = await self.generate_embedding(text)
        if embedding is None:
            return False

        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO message_embeddings (message_id, embedding, model, created_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (message_id) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    model = EXCLUDED.model,
                    created_at = NOW()
                """,
                message_id,
                embedding,
                self.model,
            )
        return True

    async def backfill_embeddings(self, limit: int = 100) -> int:
        """Generate embeddings for messages that don't have them yet."""
        async with get_connection() as conn:
            # Find messages without embeddings
            rows = await conn.fetch(
                """
                SELECT m.id, m.text
                FROM messages m
                LEFT JOIN message_embeddings me ON m.id = me.message_id
                WHERE me.id IS NULL AND m.text IS NOT NULL AND m.text != ''
                ORDER BY m.created_at DESC
                LIMIT $1
                """,
                limit,
            )

        embedded_count = 0
        for row in rows:
            if await self.embed_message(row['id'], row['text']):
                embedded_count += 1

        logger.info(f'Generated embeddings for {embedded_count}/{len(rows)} messages')
        return embedded_count

    async def get_embedding_stats(self) -> dict[str, Any]:
        """Get statistics about embeddings."""
        async with get_connection() as conn:
            total_messages = await conn.fetchval('SELECT COUNT(*) FROM messages')
            embedded_messages = await conn.fetchval('SELECT COUNT(*) FROM message_embeddings')

        return {
            'total_messages': total_messages,
            'embedded_messages': embedded_messages,
            'coverage_pct': (embedded_messages / total_messages * 100) if total_messages > 0 else 0,
        }
