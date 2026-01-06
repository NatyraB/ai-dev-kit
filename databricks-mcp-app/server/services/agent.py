"""Claude Code Agent service for managing agent sessions.

Uses the claude-code-sdk to create and manage Claude Code agent sessions
with directory-scoped file permissions and Databricks MCP tools.
"""

import logging
from pathlib import Path
from typing import AsyncIterator

from claude_code_sdk import ClaudeCodeOptions, query
from claude_code_sdk.types import (
  AssistantMessage,
  ResultMessage,
  StreamEvent,
  SystemMessage,
  TextBlock,
  ThinkingBlock,
  ToolResultBlock,
  ToolUseBlock,
  UserMessage,
)

from .backup_manager import ensure_project_directory as _ensure_project_directory
from .mcp_client import get_databricks_mcp_config, get_databricks_mcp_tools
from .system_prompt import get_system_prompt

logger = logging.getLogger(__name__)

# Built-in Claude Code tools
BUILTIN_TOOLS = [
  'Read',
  'Write',
  'Edit',
  'Bash',
  'Glob',
  'Grep',
  'Skill',  # For loading skills
]


def get_project_directory(project_id: str) -> Path:
  """Get the directory path for a project.

  If the directory doesn't exist, attempts to restore from backup.
  If no backup exists, creates an empty directory.

  Args:
      project_id: The project UUID

  Returns:
      Path to the project directory
  """
  return _ensure_project_directory(project_id)


async def stream_agent_response(
  project_id: str,
  message: str,
  session_id: str | None = None,
) -> AsyncIterator[dict]:
  """Stream Claude agent response with all event types.

  Uses the simple query() function for stateless interactions.
  Yields normalized event dicts for the frontend.

  Args:
      project_id: The project UUID
      message: User message to send
      session_id: Optional session ID for resuming conversations

  Yields:
      Event dicts with 'type' field for frontend consumption
  """
  project_dir = get_project_directory(project_id)

  if session_id:
    logger.info(f'Resuming session {session_id} in {project_dir}: {message[:100]}...')
  else:
    logger.info(f'Starting new session in {project_dir}: {message[:100]}...')

  # Build allowed tools list
  allowed_tools = BUILTIN_TOOLS.copy()

  # Get MCP server config and tools if Databricks is configured
  mcp_servers = get_databricks_mcp_config()
  if mcp_servers:
    # Dynamically fetch available tools from MCP server (cached after first call)
    databricks_tools = await get_databricks_mcp_tools()
    if databricks_tools:
      allowed_tools.extend(databricks_tools)
      logger.info(f'Databricks MCP tools enabled: {len(databricks_tools)} tools')

  # Generate system prompt with available skills
  system_prompt = get_system_prompt()

  options = ClaudeCodeOptions(
    cwd=str(project_dir),
    allowed_tools=allowed_tools,
    permission_mode='acceptEdits',  # Auto-accept file edits
    resume=session_id,  # Resume from previous session if provided
    mcp_servers=mcp_servers,  # Add Databricks MCP server
    system_prompt=system_prompt,  # Databricks-focused system prompt
  )

  try:
    async for msg in query(prompt=message, options=options):
      # Handle different message types
      if isinstance(msg, AssistantMessage):
        # Process content blocks
        for block in msg.content:
          if isinstance(block, TextBlock):
            yield {
              'type': 'text',
              'text': block.text,
            }
          elif isinstance(block, ThinkingBlock):
            yield {
              'type': 'thinking',
              'thinking': block.thinking,
            }
          elif isinstance(block, ToolUseBlock):
            yield {
              'type': 'tool_use',
              'tool_id': block.id,
              'tool_name': block.name,
              'tool_input': block.input,
            }
          elif isinstance(block, ToolResultBlock):
            yield {
              'type': 'tool_result',
              'tool_use_id': block.tool_use_id,
              'content': block.content,
              'is_error': block.is_error,
            }

      elif isinstance(msg, ResultMessage):
        yield {
          'type': 'result',
          'session_id': msg.session_id,
          'duration_ms': msg.duration_ms,
          'total_cost_usd': msg.total_cost_usd,
          'is_error': msg.is_error,
          'num_turns': msg.num_turns,
        }

      elif isinstance(msg, SystemMessage):
        yield {
          'type': 'system',
          'subtype': msg.subtype,
          'data': msg.data if hasattr(msg, 'data') else None,
        }

      elif isinstance(msg, UserMessage):
        # Echo of user message, can skip or forward
        pass

      elif isinstance(msg, StreamEvent):
        # Raw stream event
        yield {
          'type': 'stream_event',
          'event': msg.event,
          'session_id': msg.session_id,
        }

  except Exception as e:
    logger.error(f'Error during Claude query: {e}')
    yield {
      'type': 'error',
      'error': str(e),
    }


# Keep simple aliases for backward compatibility
async def simple_query(project_id: str, message: str) -> AsyncIterator[dict]:
  """Simple stateless query to Claude within a project directory."""
  async for event in stream_agent_response(project_id, message):
    yield event
