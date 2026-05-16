"""Hardware-agent custom visualizer.

FastAPI + Jinja + htmx + SSE. Reads state from hw_agent/.state.json plus
agent-written JSON under hw_agent/.live/data/<project>/<subsystem>.json.

Run: `hw-vis` (entry point) or `python -m hw_agent.visualizer.server`.
"""
