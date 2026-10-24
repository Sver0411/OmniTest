"""Named server credentials and fail-closed project authorization for the HTTP API."""

import hmac
import os
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from pydantic import Field

from omnitest.core.ir import Model
from omnitest.db import Resource, Run
from omnitest.services.resources import SCHEMAS


class Credential(Model):
    name: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    token_env: str = Field(pattern=r"^OMNITEST_ACCESS_[A-Z0-9_]+$")
    role: Literal["admin", "editor", "operator", "viewer"]
    project_ids: list[str] | None = Field(default_factory=list, max_length=500)
    enabled: bool = True


@dataclass(frozen=True)
class Principal:
    name: str
    role: str
    project_ids: frozenset[str] | None = None

    @property
    def admin(self):
        return self.role == "admin"

    @property
    def manage(self):
        return self.role in {"admin", "editor"}

    @property
    def execute(self):
        return self.role in {"admin", "editor", "operator"}

    def visible(self, project_id):
        return self.admin or self.project_ids is None or project_id in self.project_ids

    def check_project(self, project_id):
        if not isinstance(project_id, str) or not self.visible(project_id):
            raise HTTPException(403, "Project access denied")

    def describe(self):
        return {
            "name": self.name,
            "role": self.role,
            "project_ids": sorted(self.project_ids) if self.project_ids is not None else None,
            "permissions": {"admin": self.admin, "manage": self.manage, "execute": self.execute},
        }


class AccessControl:
    def __init__(self, settings, db):
        self.settings, self.db = settings, db
        self.credentials = []
        names, tokens = set(), set()
        if settings.api_token:
            self.credentials.append((settings.api_token, Principal("administrator", "admin")))
            tokens.add(settings.api_token)
        for item in settings.access_tokens:
            credential = Credential.model_validate(item)
            if credential.name in names:
                raise ValueError("Credential names must be unique")
            names.add(credential.name)
            if not credential.enabled:
                continue
            token = os.getenv(credential.token_env, "")
            if len(token) < 16:
                raise ValueError(
                    f"Credential {credential.name} requires an environment token of at least 16 characters"
                )
            if token in tokens:
                raise ValueError("Credentials must not share the same token")
            tokens.add(token)
            projects = (
                frozenset(credential.project_ids) if credential.project_ids is not None else None
            )
            if credential.role == "admin" and projects:
                raise ValueError("Administrator credentials cannot be project scoped")
            if credential.role == "admin":
                projects = None
            self.credentials.append((token, Principal(credential.name, credential.role, projects)))

    def authenticate(self, authorization):
        if not self.settings.authentication:
            return Principal("local", "admin")
        supplied = (
            authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        )
        principal = None
        for token, candidate in self.credentials:
            if hmac.compare_digest(supplied.encode(), token.encode()):
                principal = candidate
        if principal is None:
            raise HTTPException(401, "Invalid API token")
        return principal

    def project_for(self, model, identity, kind=None):
        with self.db.session() as session:
            row = session.get(model, identity)
            if not row or kind is not None and row.kind != kind:
                raise HTTPException(404, "Resource not found")
            return row.id if kind == "projects" else row.project_id

    async def authorize(self, request, principal):
        if principal.admin:
            return
        path, method = request.url.path, request.method
        parts = path.strip("/").split("/")
        read = method in {"GET", "HEAD"}
        project = request.query_params.get("project_id")
        if project:
            principal.check_project(project)
        if read and path in {"/api/session", "/api/settings", "/api/adapters", "/api/dashboard"}:
            return
        if path == "/api/cases/import":
            if not principal.manage:
                raise HTTPException(403, "Editor role required")
            payload = await self.payload(request)
            project = payload.get("project_id")
        elif len(parts) >= 3 and parts[1] == "resources":
            kind = parts[2]
            if kind not in SCHEMAS:
                raise HTTPException(403, "Resource access denied")
            if not read and (
                not principal.manage or kind == "projects" and method in {"POST", "DELETE"}
            ):
                raise HTTPException(403, "Resource modification denied")
            if len(parts) == 4:
                project = self.project_for(Resource, parts[3], kind)
            elif not read:
                project = (await self.payload(request)).get("project_id")
            elif project is None:
                return
        elif len(parts) >= 2 and parts[1] == "runs":
            if not read and not principal.execute:
                raise HTTPException(403, "Execution permission required")
            if len(parts) >= 3:
                project = self.project_for(Run, parts[2])
            elif not read:
                project = (await self.payload(request)).get("project_id")
            elif project is None:
                return
        elif len(parts) == 6 and parts[1] == "evidence" and read:
            project = self.project_for(Run, parts[2])
        else:
            raise HTTPException(403, "Administrator role required")
        principal.check_project(project)
        request.state.project_id = project

    @staticmethod
    async def payload(request):
        try:
            value = await request.json()
        except ValueError as exc:
            raise HTTPException(422, "Invalid JSON payload") from exc
        if not isinstance(value, dict):
            raise HTTPException(422, "JSON object required")
        return value
