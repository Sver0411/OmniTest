"""Run administrator-owned argv profiles without shell interpretation."""

import asyncio
import os
import re
import signal
import time

from omnitest.core.security import contained

from .base import AdapterInfo


class CliAdapter:
    info = AdapterInfo(
        "cli",
        "1.0",
        ["run", "stdout", "stderr", "exit_code", "files"],
        ["administrator program profile"],
        ["cli"],
    )

    async def check(self, context):
        profile = context.target["config"]["program"]
        self.profile = context.settings.cli_programs.get(profile)
        if not self.profile:
            raise ValueError("Program is not in administrator allowlist")
        self.process = None

    async def execute(self, step, context):
        if step.action != "run":
            raise ValueError(f"Unsupported CLI action: {step.action}")
        args = step.config.get("args", [])
        patterns = self.profile.get("arguments", [])
        if not self.profile.get("min_args", len(patterns)) <= len(args) <= len(patterns):
            raise ValueError("Argument count does not match program policy")
        if not all(isinstance(a, str) and re.fullmatch(p, a) for a, p in zip(args, patterns)):
            raise ValueError("Argument rejected by program policy")
        start = time.monotonic()
        env = {k: v for k, v in os.environ.items() if not k.startswith("OMNITEST_")}
        env.update(self.profile.get("env", {}))
        self.process = await asyncio.create_subprocess_exec(
            *self.profile["argv"],
            *args,
            cwd=context.evidence_dir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )

        async def read(stream):
            output = bytearray()
            while chunk := await stream.read(8192):
                output.extend(chunk)
                if len(output) > 1_000_000:
                    raise ValueError("Process output exceeds 1 MB limit")
            return bytes(output).decode("utf-8", errors="replace").replace("\r\n", "\n")

        try:
            stdout, stderr = await asyncio.gather(
                read(self.process.stdout), read(self.process.stderr)
            )
            code = await self.process.wait()
        finally:
            await self.close()
        files = {}
        for name in step.config.get("files", []):
            path = contained(context.evidence_dir, name)
            files[name] = {"exists": path.is_file()}
            if path.is_file():
                if path.stat().st_size > 1_000_000:
                    raise ValueError("Evidence file exceeds 1 MB limit")
                files[name]["text"] = path.read_text(encoding="utf-8")
                context.evidence.append(name)
        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": code,
            "duration": time.monotonic() - start,
            "files": files,
        }

    async def close(self):
        if getattr(self, "process", None) and self.process.returncode is None:
            if os.name != "nt":
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                self.process.kill()
            await self.process.wait()
