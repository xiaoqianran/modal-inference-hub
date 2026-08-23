from __future__ import annotations

import modal


def test_credentials(token_id: str, token_secret: str) -> None:
    client = modal.Client.from_credentials(token_id.strip(), token_secret.strip())
    client.hello()
