"""Configuration management for Slack Assistant."""

import os
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass
class Config:
    """Application configuration loaded from environment variables."""

    # Slack
    slack_user_token: str = field(default_factory=lambda: os.environ.get('SLACK_USER_TOKEN', ''))

    # Database
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            'DATABASE_URL', 'postgresql://slack_assistant:slack_assistant@localhost:5432/slack_assistant'
        )
    )

    # Polling
    poll_interval_seconds: int = field(default_factory=lambda: int(os.environ.get('POLL_INTERVAL_SECONDS', '60')))

    # Embeddings (for future use)
    embedding_model: str = field(default_factory=lambda: os.environ.get('EMBEDDING_MODEL', 'text-embedding-ada-002'))

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []

        if not self.slack_user_token:
            errors.append('SLACK_USER_TOKEN is required')
        elif not self.slack_user_token.startswith('xoxp-'):
            errors.append('SLACK_USER_TOKEN should be a user OAuth token (xoxp-...)')

        if self.poll_interval_seconds < 10:
            errors.append('POLL_INTERVAL_SECONDS should be at least 10 to avoid rate limits')

        return errors


@lru_cache
def get_config() -> Config:
    """Get cached configuration instance."""
    return Config()
