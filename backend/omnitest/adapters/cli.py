"""Run administrator-owned argv profiles without shell interpretation."""

import asyncio
import os
import re
import signal
import time

from omnitest.core.security import contained

from .base import AdapterInfo

SYSTEM_ENV = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
    "SYSTEMDRIVE",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
}


def process_environment(profile):
    inherited = set(profile.get("inherit_env", []))
    return {
        **{
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith("OMNITEST_")
            and (key.upper() in SYSTEM_ENV or key in inherited)
        },
        **profile.get("env", {}),
    }


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
        self.process = await asyncio.create_subprocess_exec(
            *self.profile["argv"],
            *args,
            cwd=context.evidence_dir,
            env=process_environment(self.profile),
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

        readers = [
            asyncio.create_task(read(stream))
            for stream in [self.process.stdout, self.process.stderr]
        ]
        try:
            stdout, stderr = await asyncio.gather(*readers)
            code = await self.process.wait()
        finally:
            for reader in readers:
                if not reader.done():
                    reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
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
        if getattr(self, "process", None):
            if os.name != "nt":
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif self.process.returncode is None:
                terminator = await asyncio.create_subprocess_exec(
                    os.path.join(os.environ["SystemRoot"], "System32", "taskkill.exe"),
                    "/PID",
                    str(self.process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await terminator.wait()
                if self.process.returncode is None:
                    self.process.kill()
            await self.process.communicate()
            self.process = None
