"""Slack integration module."""

from slack_assistant.slack.client import SlackClient
from slack_assistant.slack.poller import SlackPoller


__all__ = ['SlackClient', 'SlackPoller']
