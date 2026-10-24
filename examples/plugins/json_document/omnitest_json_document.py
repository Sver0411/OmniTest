"""Independent plugin package performing actual scoped JSON file inspection."""

import json

from pydantic import Field

from omnitest.core.security import contained
from omnitest.sdk import AdapterInfo, Model


class Config(Model):
    path: str = Field(min_length=1, max_length=4096)


class JsonDocumentAdapter:
    info = AdapterInfo("json-document", "0.2.0", ["read"], ["UTF-8 JSON file"], ["file"])

    async def check(self, context):
        config = Config.model_validate(context.target["config"])
        self.path = contained(context.settings.file_root, config.path)
        if self.path.stat().st_size > 1_000_000:
            raise ValueError("JSON document exceeds 1 MB limit")

    async def execute(self, step, context):
        if step.action != "read" or step.config:
            raise ValueError("JSON document plugin supports read without step configuration")
        return {"document": json.loads(self.path.read_text(encoding="utf-8"))}

    async def close(self):
        self.path = None
