"""OpenAI-compatible transport with explicit credentials and bounded responses."""

import json
import os

import httpx
from pydantic import Field, field_validator

from omnitest.core.ir import Model
from omnitest.core.security import check_url


class ProviderConfig(Model):
    name: str = Field(min_length=1, max_length=100)
    base_url: str
    model: str = Field(min_length=1, max_length=200)
    key_env: str = ""
    timeout: float = Field(default=30, ge=1, le=120)

    @field_validator("key_env")
    @classmethod
    def secret_reference(cls, value):
        if value and not value.startswith("OMNITEST_SECRET_"):
            raise ValueError("Credentials must reference OMNITEST_SECRET_* environment variables")
        return value


class CompatibleProvider:
    def __init__(self, config, settings):
        self.config = config
        self.settings = settings

    async def complete(self, system, context):
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        check_url(url, self.settings.allowed_hosts)
        if url.startswith("http:") and not url.startswith(
            ("http://localhost:", "http://127.0.0.1:")
        ):
            raise ValueError("Remote providers require HTTPS")
        key = os.environ.get(self.config.key_env) if self.config.key_env else None
        if self.config.key_env and not key:
            raise ValueError("Provider credential environment variable is missing")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        async with httpx.AsyncClient(
            timeout=self.config.timeout, trust_env=False, follow_redirects=False
        ) as client:
            try:
                async with client.stream(
                    "POST",
                    url,
                    headers=headers,
                    json={
                        "model": self.config.model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                        ],
                        "response_format": {"type": "json_object"},
                    },
                ) as response:
                    if response.status_code != 200:
                        raise ValueError(f"Provider returned HTTP {response.status_code}")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 2_000_000:
                            raise ValueError("Provider response exceeds 2 MB")
            except httpx.HTTPError:
                raise ValueError("Provider request failed or timed out") from None
        try:
            payload = json.loads(data)
            return json.loads(payload["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError):
            raise ValueError("Provider must return a JSON object in message.content") from None
