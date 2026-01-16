#!/usr/bin/env python3
"""CLI entry point for Slack Assistant."""

import asyncio
import logging
import sys
from datetime import datetime

import click

from slack_assistant.config import get_config
from slack_assistant.db.connection import close_pool, get_pool
from slack_assistant.db.repository import Repository
from slack_assistant.services.status import Priority, StatusService
from slack_assistant.slack.client import SlackClient
from slack_assistant.slack.poller import SlackPoller


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


def run_async(coro):
    """Run an async coroutine."""
    return asyncio.run(coro)


@click.group()
@click.option('--debug', is_flag=True, help='Enable debug logging')
def cli(debug: bool):
    """Slack Assistant - AI-powered Slack integration tool."""
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)


@cli.command()
def daemon():
    """Start the background polling daemon."""
    config = get_config()
    errors = config.validate()
    if errors:
        for error in errors:
            click.echo(f'Config error: {error}', err=True)
        sys.exit(1)

    async def run_daemon():
        client = SlackClient(config.slack_user_token)
        repository = Repository()

        try:
            # Ensure database is ready
            await get_pool()
            logger.info('Connected to database')

            poller = SlackPoller(client, repository)
            await poller.start()
        except KeyboardInterrupt:
            logger.info('Interrupted by user')
        finally:
            await close_pool()

    click.echo('Starting Slack Assistant daemon...')
    run_async(run_daemon())


@cli.command()
@click.option('--hours', default=24, help='Hours to look back (default: 24)')
def status(hours: int):
    """Get status of items needing attention."""
    config = get_config()
    errors = config.validate()
    if errors:
        for error in errors:
            click.echo(f'Config error: {error}', err=True)
        sys.exit(1)

    async def get_status():
        client = SlackClient(config.slack_user_token)
        repository = Repository()

        try:
            await get_pool()
            if not await client.authenticate():
                click.echo('Failed to authenticate with Slack', err=True)
                sys.exit(1)

            service = StatusService(client, repository)
            result = await service.get_status(hours_back=hours)

            _print_status(result)

        finally:
            await close_pool()

    run_async(get_status())


def _print_status(status):
    """Print formatted status report."""
    click.echo()
    click.echo(click.style('=' * 60, fg='blue'))
    click.echo(click.style('  SLACK STATUS REPORT', bold=True))
    click.echo(click.style(f'  Generated: {status.generated_at.strftime("%Y-%m-%d %H:%M")}', dim=True))
    click.echo(click.style('=' * 60, fg='blue'))
    click.echo()

    priority_styles = {
        Priority.CRITICAL: ('red', 'CRITICAL - Mentions'),
        Priority.HIGH: ('yellow', 'HIGH - Direct Messages'),
        Priority.MEDIUM: ('cyan', 'MEDIUM - Thread Replies'),
        Priority.LOW: ('white', 'LOW - Channel Messages'),
    }

    by_priority = status.by_priority
    total_items = 0

    for priority in Priority:
        items = by_priority[priority]
        if not items:
            continue

        color, label = priority_styles[priority]
        click.echo(click.style(f'\n{label} ({len(items)})', fg=color, bold=True))
        click.echo(click.style('-' * 40, fg=color))

        for item in items[:10]:  # Limit to 10 per category
            _print_status_item(item, color)
            total_items += 1

        if len(items) > 10:
            click.echo(click.style(f'  ... and {len(items) - 10} more', dim=True))

    if total_items == 0:
        click.echo(click.style('No items need attention!', fg='green', bold=True))

    # Reminders section
    if status.reminders:
        click.echo(click.style('\nLATER (Reminders)', fg='magenta', bold=True))
        click.echo(click.style('-' * 40, fg='magenta'))

        for reminder in status.reminders[:5]:
            time_str = ''
            if reminder.get('time'):
                try:
                    dt = datetime.fromisoformat(reminder['time'])
                    time_str = f' ({dt.strftime("%m/%d %H:%M")})'
                except (ValueError, TypeError):
                    pass

            text = reminder.get('text', '')[:60]
            click.echo(f'  - {text}{time_str}')

        if len(status.reminders) > 5:
            click.echo(click.style(f'  ... and {len(status.reminders) - 5} more', dim=True))

    click.echo()


def _print_status_item(item, color):
    """Print a single status item."""
    channel = f'#{item.channel_name}' if item.channel_name else item.channel_id
    user = item.user_name or item.user_id or 'unknown'
    time_str = item.timestamp.strftime('%m/%d %H:%M') if item.timestamp else ''

    click.echo(f'  {click.style(channel, fg=color)} - {user} ({time_str})')
    click.echo(f'    {item.text_preview}')
    click.echo(click.style(f'    {item.link}', dim=True))
    click.echo()


@cli.command()
def sync():
    """Run a one-time sync of all Slack data."""
    config = get_config()
    errors = config.validate()
    if errors:
        for error in errors:
            click.echo(f'Config error: {error}', err=True)
        sys.exit(1)

    async def run_sync():
        client = SlackClient(config.slack_user_token)
        repository = Repository()

        try:
            await get_pool()
            logger.info('Connected to database')

            if not await client.authenticate():
                click.echo('Failed to authenticate with Slack', err=True)
                sys.exit(1)

            poller = SlackPoller(client, repository)
            await poller._sync_channels()
            await poller._sync_all_messages()

            click.echo('Sync complete!')

        finally:
            await close_pool()

    click.echo('Running one-time sync...')
    run_async(run_sync())


@cli.command()
def reminders():
    """Sync and display reminders (Later section)."""
    config = get_config()
    errors = config.validate()
    if errors:
        for error in errors:
            click.echo(f'Config error: {error}', err=True)
        sys.exit(1)

    async def sync_reminders():
        client = SlackClient(config.slack_user_token)
        repository = Repository()

        try:
            await get_pool()

            if not await client.authenticate():
                click.echo('Failed to authenticate with Slack', err=True)
                sys.exit(1)

            # Fetch reminders from Slack API
            slack_reminders = await client.get_reminders()
            click.echo(f'Found {len(slack_reminders)} reminders from Slack')

            # Store in database
            from slack_assistant.db.models import Reminder

            for r in slack_reminders:
                reminder = Reminder(
                    id=r['id'],
                    user_id=r.get('user', client.user_id),
                    text=r.get('text'),
                    time=datetime.fromtimestamp(r['time']) if r.get('time') else None,
                    complete_ts=datetime.fromtimestamp(r['complete_ts']) if r.get('complete_ts') else None,
                    recurring=r.get('recurring', False),
                    metadata={
                        k: v
                        for k, v in r.items()
                        if k not in ('id', 'user', 'text', 'time', 'complete_ts', 'recurring')
                    },
                )
                await repository.upsert_reminder(reminder)

            # Display pending reminders
            pending = await repository.get_pending_reminders(client.user_id)
            if pending:
                click.echo(click.style('\nPending Reminders:', bold=True))
                for r in pending:
                    time_str = r.time.strftime('%Y-%m-%d %H:%M') if r.time else 'no time set'
                    click.echo(f'  - [{time_str}] {r.text}')
            else:
                click.echo('\nNo pending reminders.')

        finally:
            await close_pool()

    run_async(sync_reminders())


@cli.command()
@click.argument('query')
@click.option('--limit', default=10, help='Maximum number of results (default: 10)')
@click.option('--use-slack-api', is_flag=True, help='Also search using Slack API')
def search(query: str, limit: int, use_slack_api: bool):
    """Search for messages matching the query."""
    config = get_config()
    errors = config.validate()
    if errors:
        for error in errors:
            click.echo(f'Config error: {error}', err=True)
        sys.exit(1)

    async def run_search():
        from slack_assistant.services.embeddings import EmbeddingService
        from slack_assistant.services.search import SearchService

        client = SlackClient(config.slack_user_token)
        repository = Repository()

        try:
            await get_pool()

            if not await client.authenticate():
                click.echo('Failed to authenticate with Slack', err=True)
                sys.exit(1)

            embedding_service = EmbeddingService(repository)
            search_service = SearchService(client, repository, embedding_service)

            results = await search_service.search(
                query,
                limit=limit,
                use_vector=True,
                use_text=True,
                use_slack_api=use_slack_api,
            )

            if not results:
                click.echo('No results found.')
                return

            click.echo(click.style(f'\nSearch Results for "{query}":', bold=True))
            click.echo(click.style('-' * 50, dim=True))

            for i, result in enumerate(results, 1):
                channel = f'#{result.channel_name}' if result.channel_name else result.message.channel_id
                user = result.user_name or result.message.user_id or 'unknown'
                time_str = result.message.created_at.strftime('%m/%d %H:%M') if result.message.created_at else ''
                text_preview = (result.message.text or '')[:80]

                click.echo(f'\n{i}. {click.style(channel, fg="cyan")} - {user} ({time_str})')
                click.echo(f'   {text_preview}')
                click.echo(click.style(f'   Score: {result.score:.2f} ({result.match_type})', dim=True))
                click.echo(click.style(f'   {result.link}', dim=True))

        finally:
            await close_pool()

    run_async(run_search())


@cli.command()
@click.argument('message_link')
@click.option('--limit', default=10, help='Maximum number of related messages (default: 10)')
def context(message_link: str, limit: int):
    """Find context for a Slack message link."""
    config = get_config()
    errors = config.validate()
    if errors:
        for error in errors:
            click.echo(f'Config error: {error}', err=True)
        sys.exit(1)

    async def find_context():
        from slack_assistant.services.embeddings import EmbeddingService
        from slack_assistant.services.search import SearchService

        client = SlackClient(config.slack_user_token)
        repository = Repository()

        try:
            await get_pool()

            if not await client.authenticate():
                click.echo('Failed to authenticate with Slack', err=True)
                sys.exit(1)

            embedding_service = EmbeddingService(repository)
            search_service = SearchService(client, repository, embedding_service)

            results = await search_service.find_context(message_link, limit=limit)

            if not results:
                click.echo('No related messages found.')
                return

            click.echo(click.style('\nRelated Messages:', bold=True))
            click.echo(click.style('-' * 50, dim=True))

            for i, result in enumerate(results, 1):
                channel = f'#{result.channel_name}' if result.channel_name else result.message.channel_id
                user = result.user_name or result.message.user_id or 'unknown'
                time_str = result.message.created_at.strftime('%m/%d %H:%M') if result.message.created_at else ''
                text_preview = (result.message.text or '')[:80]

                click.echo(f'\n{i}. {click.style(channel, fg="cyan")} - {user} ({time_str})')
                click.echo(f'   {text_preview}')
                click.echo(click.style(f'   Score: {result.score:.2f}', dim=True))
                click.echo(click.style(f'   {result.link}', dim=True))

        finally:
            await close_pool()

    run_async(find_context())


def main():
    """Main entry point."""
    cli()


if __name__ == '__main__':
    main()
