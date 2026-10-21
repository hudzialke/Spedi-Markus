"""Stub integracji Trans.eu — sync w kolejnej iteracji."""

from __future__ import annotations


class TransEuClient:
    """Placeholder pod oficjalne API Trans.eu."""

    def __init__(self, *, api_key: str | None = None) -> None:
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def sync_transports(self) -> list[dict]:
        if not self.configured:
            raise RuntimeError(
                "Brak klucza API Trans.eu. Skonfiguruj dostęp, potem włącz sync."
            )
        return []
