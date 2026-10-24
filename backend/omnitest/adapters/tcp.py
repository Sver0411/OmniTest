"""Bounded TCP request/response exchanges against one authorized endpoint."""

import asyncio
import ssl

from pydantic import Field

from omnitest.core.ir import Model
from omnitest.core.security import check_url

from .base import AdapterInfo


class Endpoint(Model):
    host: str
    port: int = Field(ge=1, le=65535)
    tls: bool = False


class Exchange(Model):
    text: str | None = Field(default=None, max_length=65536)
    hex: str | None = Field(default=None, max_length=131072)
    encoding: str = "utf-8"
    read_mode: str = "line"
    read_bytes: int = Field(default=1024, ge=1, le=65536)
    timeout: float = Field(default=5, ge=0.05, le=30)


class TcpAdapter:
    info = AdapterInfo(
        "tcp", "1.0", ["exchange", "text", "binary", "tls"], ["authorized TCP endpoint"], ["tcp"]
    )

    async def check(self, context):
        cfg = Endpoint.model_validate(context.target["config"])
        host = f"[{cfg.host}]" if ":" in cfg.host else cfg.host
        check_url(f"http://{host}:{cfg.port}", context.settings.allowed_hosts)
        tls = ssl.create_default_context() if cfg.tls else None
        self.reader, self.writer = await asyncio.open_connection(
            cfg.host, cfg.port, ssl=tls, limit=65536
        )

    async def execute(self, step, context):
        if step.action != "exchange":
            raise ValueError("TCP adapter supports exchange only")
        cfg = Exchange.model_validate(step.config)
        if (cfg.text is None) == (cfg.hex is None):
            raise ValueError("Provide exactly one of text or hex")
        if cfg.read_mode not in {"line", "exact"}:
            raise ValueError("read_mode must be line or exact")
        payload = bytes.fromhex(cfg.hex) if cfg.hex is not None else cfg.text.encode(cfg.encoding)
        if len(payload) > 65536:
            raise ValueError("TCP payload exceeds 64 KB")
        async with asyncio.timeout(cfg.timeout):
            self.writer.write(payload)
            await self.writer.drain()
            if cfg.read_mode == "line":
                data = await self.reader.readuntil(b"\n")
            else:
                data = await self.reader.readexactly(cfg.read_bytes)
        return {
            "text": data.decode(cfg.encoding, errors="replace"),
            "hex": data.hex(),
            "bytes_sent": len(payload),
            "bytes_received": len(data),
        }

    async def close(self):
        if getattr(self, "writer", None):
            self.writer.close()
            try:
                await asyncio.wait_for(self.writer.wait_closed(), 2)
            except (OSError, TimeoutError):
                pass
            self.writer = None
