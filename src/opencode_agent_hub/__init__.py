# opencode-agent-hub - Multi-agent coordination daemon for OpenCode
# Copyright (c) 2025 xnoto

"""OpenCode Agent Hub - Multi-agent coordination for OpenCode."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("opencode-agent-hub")
except PackageNotFoundError:  # pragma: no cover - fallback for dev
    __version__ = "0.5.2"
