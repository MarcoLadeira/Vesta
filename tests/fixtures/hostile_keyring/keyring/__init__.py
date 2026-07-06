from __future__ import annotations


class _PopulatedKeyring:
    priority = 1

    def get_password(self, service: str, username: str) -> str:
        return "fixture-" + "credential"

    def set_password(self, service: str, username: str, password: str) -> None:
        return None

    def delete_password(self, service: str, username: str) -> None:
        return None


def get_keyring() -> _PopulatedKeyring:
    return _PopulatedKeyring()
