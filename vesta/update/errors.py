"""Stable updater errors; raw external exceptions never cross this boundary."""

from __future__ import annotations


class UpdateError(RuntimeError):
    def __init__(self, category: str, *, retriable: bool = False) -> None:
        self.category = str(category)
        self.retriable = bool(retriable)
        super().__init__(self.category)
