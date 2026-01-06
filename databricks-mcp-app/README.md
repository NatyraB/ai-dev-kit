# Databricks AI Dev Kit

A web application that provides a Claude Code agent interface with integrated Databricks tools. Users interact with Claude through a chat interface, and the agent can execute SQL queries, manage pipelines, upload files, and more on their Databricks workspace.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Web Application                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│  React Frontend (client/)           FastAPI Backend (server/)               │
│  ┌─────────────────────┐            ┌─────────────────────────────────┐     │
│  │ Chat UI             │◄──────────►│ /api/agent/invoke               │     │
│  │ Project Selector    │   SSE      │ /api/projects                   │     │
│  │ Conversation List   │            │ /api/conversations              │     │
│  └─────────────────────┘            └─────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Claude Code Session                                │
├─────────────────────────────────────────────────────────────────────────────┤
│  Each user message spawns a Claude Code agent session via claude-code-sdk   │
│                                                                              │
│  Built-in Tools:              MCP Tools (Databricks):         Skills:       │
│  ┌──────────────────┐         ┌─────────────────────────┐    ┌───────────┐  │
│  │ Read, Write, Edit│         │ execute_sql             │    │ sdp       │  │
│  │ Bash, Glob, Grep │         │ create_or_update_pipeline    │ dabs      │  │
│  │ Skill            │         │ upload_folder           │    │ sdk       │  │
│  └──────────────────┘         │ run_python_file         │    │ ...       │  │
│                               │ ...                     │    └───────────┘  │
│                               └─────────────────────────┘                   │
│                                          │                                  │
│                                          ▼                                  │
│                               ┌─────────────────────────┐                   │
│                               │ databricks-mcp-server   │                   │
│                               │ (stdio subprocess)      │                   │
│                               └─────────────────────────┘                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Databricks Workspace                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  SQL Warehouses    │    Clusters    │    Unity Catalog    │    Workspace    │
└─────────────────────────────────────────────────────────────────────────────┘
```

## How It Works

### 1. Claude Code Sessions

When a user sends a message, the backend creates a Claude Code session using the `claude-code-sdk`:

```python
from claude_code_sdk import ClaudeCodeOptions, query

options = ClaudeCodeOptions(
    cwd=str(project_dir),           # Project working directory
    allowed_tools=allowed_tools,     # Built-in + MCP tools
    permission_mode='acceptEdits',   # Auto-accept file edits
    resume=session_id,               # Resume previous conversation
    mcp_servers=mcp_servers,         # Databricks MCP server config
    system_prompt=system_prompt,     # Databricks-focused prompt
    setting_sources=['project'],     # Load skills from .claude/skills
)

async for msg in query(prompt=message, options=options):
    yield msg  # Stream to frontend
```

Key features:
- **Session Resumption**: Each conversation stores a `claude_session_id` for context continuity
- **Streaming**: All events (text, thinking, tool_use, tool_result) stream to the frontend in real-time
- **Project Isolation**: Each project has its own working directory with sandboxed file access

### 2. MCP Integration (Databricks Tools)

The agent connects to the Databricks MCP server as a stdio subprocess:

```python
mcp_servers = {
    'databricks': {
        'command': 'uv',
        'args': ['run', 'python', '-m', 'databricks_mcp_server.server'],
        'env': {'DATABRICKS_HOST': host, 'DATABRICKS_TOKEN': token},
        'defer_loading': True,  # Start server only when tools are needed
    }
}
```

On first use, the app discovers available tools by connecting to the MCP server:
- Spawns the server via stdio
- Calls `list_tools()` to get available operations
- Caches the tool list for subsequent sessions
- Tools are exposed as `mcp__databricks__<tool_name>`

### 3. Skills System

Skills provide specialized guidance for Databricks development tasks. They are markdown files with instructions and examples that Claude can load on demand.

**Skill loading flow:**
1. On startup, skills are copied from `../databricks-skills/` to `./skills/`
2. When a project is created, skills are copied to `project/.claude/skills/`
3. The agent can invoke skills using the `Skill` tool: `skill: "sdp"`

Skills include:
- **sdp**: Spark Declarative Pipelines (SDP) development
- **dabs-writer**: Databricks Asset Bundles configuration
- **databricks-python-sdk**: Python SDK patterns
- **synthetic-data-generation**: Creating test datasets

### 4. Project Persistence

Projects are stored in the local filesystem with automatic backup to PostgreSQL:

```
projects/
  <project-uuid>/
    .claude/
      skills/        # Copied skills for this project
    src/             # User's code files
    ...
```

**Backup system:**
- After each agent interaction, the project is marked for backup
- A background worker runs every 10 minutes
- Projects are zipped and stored in PostgreSQL (Lakebase)
- On access, missing projects are restored from backup

## Setup

### Prerequisites

- Python 3.11+
- Node.js 18+
- [uv](https://github.com/astral-sh/uv) package manager
- Databricks workspace with SQL warehouse
- PostgreSQL database (Lakebase) for persistence

### Environment Variables

Copy `.env.example` to `.env.local` and configure:

```bash
# Databricks configuration
DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
DATABRICKS_TOKEN=dapi...

# PostgreSQL database (Lakebase) - required for persistence
LAKEBASE_PG_URL=postgresql://user:password@host:5432/database?sslmode=require
LAKEBASE_PROJECT_ID=your-lakebase-project-id

# Projects directory (where agent works)
PROJECTS_BASE_DIR=./projects

# Environment mode
ENV=development
```

### Development

```bash
# Install dependencies
uv sync

# Install sibling packages (MCP server)
uv pip install -e ../databricks-mcp-core -e ../databricks-mcp-server

# Start development servers (backend + frontend)
./scripts/start_dev.sh
```

This starts:
- Backend: http://localhost:8000
- Frontend: http://localhost:3000

### Production Build

```bash
# Build frontend
cd client && npm run build && cd ..

# Run with uvicorn
uvicorn server.app:app --host 0.0.0.0 --port 8000
```

## Project Structure

```
databricks-mcp-app/
├── server/                 # FastAPI backend
│   ├── app.py             # Main FastAPI app
│   ├── db/                # Database models and migrations
│   │   ├── models.py      # SQLAlchemy models
│   │   └── database.py    # Session management
│   ├── routers/           # API endpoints
│   │   ├── agent.py       # /api/agent/* (invoke, etc.)
│   │   ├── projects.py    # /api/projects/*
│   │   └── conversations.py
│   └── services/          # Business logic
│       ├── agent.py       # Claude Code session management
│       ├── mcp_client.py  # MCP tool discovery
│       ├── skills_manager.py
│       ├── backup_manager.py
│       └── system_prompt.py
├── client/                # React frontend
│   ├── src/
│   │   ├── pages/         # Main pages (ProjectPage, etc.)
│   │   └── components/    # UI components
│   └── package.json
├── alembic/               # Database migrations
├── scripts/               # Utility scripts
│   └── start_dev.sh       # Development startup
├── skills/                # Cached skills (gitignored)
├── projects/              # Project working directories (gitignored)
├── pyproject.toml         # Python dependencies
└── .env.example           # Environment template
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/projects` | GET | List all projects |
| `/api/projects` | POST | Create new project |
| `/api/projects/{id}` | GET | Get project details |
| `/api/projects/{id}/conversations` | GET | List project conversations |
| `/api/conversations` | POST | Create new conversation |
| `/api/conversations/{id}` | GET | Get conversation with messages |
| `/api/agent/invoke` | POST | Send message to agent (SSE stream) |
| `/api/config/user` | GET | Get current user info |

## Related Packages

- **databricks-mcp-core**: Core MCP functionality and SQL operations
- **databricks-mcp-server**: MCP server exposing Databricks tools
- **databricks-skills**: Skill definitions for Databricks development
