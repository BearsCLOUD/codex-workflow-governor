"""CLI dispatch boundary for the self-contained workflow runtime."""

from .engine import build_parser, main

__all__ = ["build_parser", "main"]
