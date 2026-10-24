"""Fourth-domain example loaded only through package entry-point discovery."""

import hashlib

from omnitest.core.security import contained

from .base import AdapterInfo


class FileAdapter:
    info = AdapterInfo(
        "filecheck", "1.0", ["inspect", "sha256", "text"], ["authorized fixture root"], ["file"]
    )

    async def check(self, context):
        self.path = contained(context.settings.file_root, context.target["config"]["path"])

    async def execute(self, step, context):
        if step.action != "inspect":
            raise ValueError("File adapter supports inspect only")
        if not self.path.is_file():
            return {"exists": False}
        if self.path.stat().st_size > 1_000_000:
            raise ValueError("Fixture exceeds 1 MB limit")
        data = self.path.read_bytes()
        return {
            "exists": True,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "text": data.decode("utf-8"),
        }

    async def close(self):
        pass
