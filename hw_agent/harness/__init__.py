"""Declarative doctrine harness for the hardware agent.

Two layers, one source of truth (doctrine.yaml):
- Soft layer: inject relevant rules into prompt context (stage- or tool-scoped).
- Hard layer: PreToolUse hook runs validators against tool args; reject on fail.
"""
