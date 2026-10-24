"""Administrator-owned configuration; never writable through the API."""

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("OMNITEST_DATA_DIR", "data")).resolve()
    )
    allowed_hosts: set[str] = field(
        default_factory=lambda: set(
            filter(None, os.getenv("OMNITEST_ALLOWED_HOSTS", "").split(","))
        )
    )
    cli_programs: dict = field(default_factory=dict)
    file_root: Path = field(default_factory=lambda: Path("examples/fixtures").resolve())
    max_concurrency: int = 4
    api_token: str = field(default_factory=lambda: os.getenv("OMNITEST_API_TOKEN", ""))
    plugins: bool = True

    def __post_init__(self):
        self.data_dir = Path(self.data_dir).resolve()
        self.file_root = Path(self.file_root).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        config = os.getenv("OMNITEST_CONFIG")
        if config:
            value = json.loads(Path(config).read_text(encoding="utf-8"))
            self.cli_programs = value.get("cli_programs", {})
            self.allowed_hosts.update(value.get("allowed_hosts", []))
            self.file_root = Path(value.get("file_root", self.file_root)).resolve()
        tool = Path("examples/tool.py").resolve()
        if tool.is_file():
            self.cli_programs.setdefault(
                "demo-tool",
                {
                    "argv": [sys.executable, str(tool)],
                    "arguments": [
                        r"echo|sum|fail|write|sleep",
                        r"[a-zA-Z0-9 .,_-]{0,100}",
                        r"[0-9]{1,6}",
                    ],
                    "min_args": 1,
                },
            )

    @property
    def database_url(self):
        return f"sqlite:///{(self.data_dir / 'omnitest.db').as_posix()}"
