"""Keyring-backed secret storage with an env-var fallback, shared by scripts needing API keys."""

from __future__ import annotations

import getpass
import os

from toolboxcli._common.console import ok


def get_secret(service: str, username: str, env_var: str | None = None) -> str | None:
    try:
        import keyring

        value = keyring.get_password(service, username)
    except ImportError:
        value = None

    if not value and env_var:
        value = os.environ.get(env_var)

    return value


def set_secret_interactive(service: str, username: str, prompt: str = "Enter value: ") -> None:
    import keyring

    value = getpass.getpass(prompt)
    keyring.set_password(service, username, value)
    ok(f"Stored secret for {service}/{username} in the OS keychain.")
