from __future__ import annotations

import modal

_credentials: tuple[str, str] | None = None


def connect(token_id: str, token_secret: str) -> None:
    global _credentials
    token_id = token_id.strip()
    token_secret = token_secret.strip()
    modal.Client.from_credentials(token_id, token_secret).hello()
    _credentials = (token_id, token_secret)


def disconnect() -> None:
    global _credentials
    _credentials = None


def connected() -> bool:
    return _credentials is not None
