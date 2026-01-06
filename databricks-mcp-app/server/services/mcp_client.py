"""MCP client utilities for discovering available tools.

Connects to MCP servers to dynamically fetch tool lists.
"""

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from typing import Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

# Cached tool names (fetched once on first use)
_cached_tools: Optional[list[str]] = None
_cache_lock = asyncio.Lock()


def get_databricks_mcp_config() -> dict | None:
  """Get MCP server configuration for Databricks tools.

  Returns None if Databricks is not configured.
  """
  host = os.environ.get('DATABRICKS_HOST')
  token = os.environ.get('DATABRICKS_TOKEN')

  if not host or not token:
    return None

  return {
    'databricks': {
      'command': 'python',
      'args': ['-m', 'databricks_mcp_server.server'],
      'env': {
        'DATABRICKS_HOST': host,
        'DATABRICKS_TOKEN': token,
      },
      'defer_loading': True,  # Delay server startup until tools are needed
    }
  }


async def _fetch_mcp_tools(command: str, args: list[str], env: dict[str, str]) -> list[str]:
  """Start an MCP server via stdio and return all tool names.

  Args:
      command: Command to run (e.g., 'uv')
      args: Command arguments
      env: Environment variables for the subprocess

  Returns:
      List of tool names from the server
  """
  # Merge with current environment
  full_env = {**os.environ, **env}

  logger.info(f'Starting MCP server: {command} {args}')

  async with AsyncExitStack() as stack:
    # Start server process over stdio
    logger.info('Creating StdioServerParameters...')
    params = StdioServerParameters(
      command=command,
      args=args,
      env=full_env,
    )

    logger.info('Connecting to MCP server via stdio...')
    stdio_transport = await stack.enter_async_context(stdio_client(params))
    read_stream, write_stream = stdio_transport
    logger.info('Connected to MCP server')

    # Open a client session and initialize
    logger.info('Creating client session...')
    session = await stack.enter_async_context(
      ClientSession(read_stream, write_stream)
    )

    logger.info('Initializing MCP session...')
    await session.initialize()
    logger.info('MCP session initialized')

    # Ask server for tools
    logger.info('Calling list_tools()...')
    resp = await session.list_tools()
    tool_names = [tool.name for tool in resp.tools]

    logger.info(f'Discovered {len(tool_names)} MCP tools: {tool_names}')
    return tool_names


def _build_allowed_tools(server_name: str, mcp_tool_names: list[str]) -> list[str]:
  """Convert MCP tool names to Claude Code allowed_tools format.

  Args:
      server_name: Name of the MCP server (e.g., 'databricks')
      mcp_tool_names: List of tool names from the server

  Returns:
      List of tool names in mcp__{server_name}__{tool_name} format
  """
  return [f'mcp__{server_name}__{tool_name}' for tool_name in mcp_tool_names]


async def get_databricks_mcp_tools() -> list[str]:
  """Get Databricks MCP tools, fetching from server on first call.

  Returns empty list if Databricks is not configured.
  Results are cached for subsequent calls.

  Returns:
      List of tool names in mcp__databricks__* format
  """
  global _cached_tools

  # Get config (returns None if not configured)
  config = get_databricks_mcp_config()
  if not config:
    logger.warning('Databricks not configured (DATABRICKS_HOST/TOKEN not set)')
    return []

  # Return cached tools if available
  if _cached_tools is not None:
    return _cached_tools

  # Fetch tools with lock to prevent concurrent fetches
  async with _cache_lock:
    # Double-check after acquiring lock
    if _cached_tools is not None:
      return _cached_tools

    try:
      logger.info('Fetching Databricks MCP tools from server...')
      server_config = config['databricks']
      mcp_tool_names = await _fetch_mcp_tools(
        command=server_config['command'],
        args=server_config['args'],
        env=server_config['env'],
      )
      _cached_tools = _build_allowed_tools('databricks', mcp_tool_names)
      logger.info(f'Cached {len(_cached_tools)} Databricks MCP tools')
      return _cached_tools

    except Exception as e:
      logger.error(f'Failed to fetch MCP tools: {e}')
      return []


def clear_tools_cache() -> None:
  """Clear the cached tools list (useful for testing)."""
  global _cached_tools
  _cached_tools = None
