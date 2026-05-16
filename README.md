# hw-toolkit

Minimal hardware-workflow scaffold for Claude Code.

## Folders

- [hw_agent/](hw_agent/) for the main hardware agent code.
- [hw-router-service/](hw-router-service/) for the FreeRouting microservice.
- [skills/](skills/) for task-specific hardware skills.
- [.claude-plugin/](.claude-plugin/) for Claude Code plugin metadata.

## Status

This workspace now contains the imported agent package and router service at the root, with skills kept separate.

## MCP Servers

The MCP servers (router, designer, live-edit) are configured in [.mcp.json](.mcp.json) and auto-launch via stdio.

**Prerequisites:**
- Python 3.11+
- Poetry or pip (install dependencies from `hw_agent/pyproject.toml`)
- Docker (for `hw-router-service`)

**Setup:**
```bash
# Install hw_agent dependencies
cd hw_agent
pip install -e .

# Start the router service (separate terminal)
cd ../hw-router-service
docker compose up -d
```

The MCP servers will auto-connect when you open the workspace in Claude Code.
