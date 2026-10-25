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
    appium_profiles: dict = field(default_factory=dict)
    http_faults: dict = field(default_factory=dict)
    file_root: Path = field(default_factory=lambda: Path("examples/fixtures").resolve())
    database_root: Path | None = None
    grpc_descriptor_root: Path | None = None
    max_concurrency: int = 4
    api_token: str = field(default_factory=lambda: os.getenv("OMNITEST_API_TOKEN", ""))
    plugins: bool = True
    access_tokens: list[dict] = field(default_factory=list)
    project_secrets: dict[str, list[str]] = field(default_factory=dict)
    remote_nodes: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.data_dir = Path(self.data_dir).resolve()
        self.file_root = Path(self.file_root).resolve()
        self.database_root = Path(self.database_root or self.data_dir / "fixtures").resolve()
        self.grpc_descriptor_root = Path(
            self.grpc_descriptor_root or self.data_dir / "descriptors"
        ).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        config = os.getenv("OMNITEST_CONFIG")
        if config:
            value = json.loads(Path(config).read_text(encoding="utf-8"))
            self.cli_programs = value.get("cli_programs", {})
            self.appium_profiles = value.get("appium_profiles", {})
            self.http_faults = value.get("http_faults", {})
            self.access_tokens = value.get("access_tokens", self.access_tokens)
            self.project_secrets = value.get("project_secrets", self.project_secrets)
            self.remote_nodes = value.get("remote_nodes", self.remote_nodes)
            self.allowed_hosts.update(value.get("allowed_hosts", []))
            self.file_root = Path(value.get("file_root", self.file_root)).resolve()
            self.database_root = Path(value.get("database_root", self.database_root)).resolve()
            self.grpc_descriptor_root = Path(
                value.get("grpc_descriptor_root", self.grpc_descriptor_root)
            ).resolve()
        if not isinstance(self.project_secrets, dict) or any(
            not isinstance(project, str)
            or not isinstance(names, list)
            or any(
                not isinstance(name, str) or not name.startswith("OMNITEST_SECRET_")
                for name in names
            )
            for project, names in self.project_secrets.items()
        ):
            raise ValueError("project_secrets must map project IDs to OMNITEST_SECRET_ name lists")
        if self.remote_nodes and not self.authentication:
            raise ValueError("Remote execution nodes require management API credentials")
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
    def authentication(self):
        return bool(self.api_token or self.access_tokens)

    @property
    def database_url(self):
        return f"sqlite:///{(self.data_dir / 'omnitest.db').as_posix()}"
