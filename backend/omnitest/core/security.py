"""Execution scope, interpolation and secret redaction."""

import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import urlsplit


def contained(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Path escapes configured root")
    return path


def check_url(url: str, allowed_hosts: set[str]) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError("Only HTTP(S) targets without embedded credentials are supported")
    if parts.hostname not in allowed_hosts and parts.hostname != "localhost":
        try:
            local = ipaddress.ip_address(parts.hostname).is_loopback
        except ValueError:
            local = False
        if not local:
            raise ValueError(
                "Remote host requires administrator allowlist; use a literal loopback IP for local targets"
            )
    return f"{parts.scheme}://{parts.netloc}"


def render(value, variables: dict, allowed_secrets=None):
    if isinstance(value, dict):
        return {k: render(v, variables, allowed_secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, variables, allowed_secrets) for v in value]
    if not isinstance(value, str):
        return value

    def lookup(key):
        if key.startswith("secret."):
            name = key[7:]
            if not name.startswith("OMNITEST_SECRET_"):
                raise ValueError("Secret must use OMNITEST_SECRET_ prefix")
            if allowed_secrets is not None and name not in allowed_secrets:
                raise ValueError("Secret is not authorized for this project")
            return os.environ[name]
        return variables[key]

    full = re.fullmatch(r"\$\{([^}]+)\}", value)
    if full:
        return lookup(full[1])
    return re.sub(r"\$\{([^}]+)\}", lambda m: str(lookup(m[1])), value)


class Redactor:
    def __init__(self, extra=()):
        self.secrets = [
            v
            for k, v in os.environ.items()
            if v
            and (
                k == "OMNITEST_API_TOKEN"
                or k.startswith(("OMNITEST_SECRET_", "OMNITEST_ACCESS_", "OMNITEST_NODE_"))
            )
        ]
        self.secrets.extend(str(v) for v in extra if v)
        self.secrets = sorted(set(self.secrets), key=len, reverse=True)

    def clean(self, value):
        if isinstance(value, dict):
            return {
                k: "[REDACTED]"
                if re.search(r"password|token|secret|authorization|cookie|api.?key", k, re.I)
                else self.clean(v)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
            value = re.sub(r"omt_[A-Za-z0-9_-]{43}", "[REDACTED]", value)
            value = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[REDACTED]", value)
        return value
