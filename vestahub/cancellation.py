"""Stable cancellation exceptions shared across Vesta runner revisions."""

from __future__ import annotations


class LocalRunCancelled(Exception):
    """The user stopped a local model call mid-flight."""
