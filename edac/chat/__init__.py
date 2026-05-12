"""EDAC Chat — conversational agent layer.

Provides:
  ChatAgent      — event-driven conversational agent
  ChatStore      — session management for multi-turn conversations
  chat topics    — "chat.{session_id}.input" / "chat.{session_id}.output"
"""

from edac.chat.agent import ChatAgent
from edac.chat.store import ChatStore, ChatSession, ChatMessage
from edac.chat.sqlite_store import SqliteChatStore

__all__ = ["ChatAgent", "ChatStore", "ChatSession", "ChatMessage", "SqliteChatStore"]
