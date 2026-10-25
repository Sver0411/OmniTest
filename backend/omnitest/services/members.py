"""Administrator-managed members, hashed API credentials and additive team grants."""

import hashlib
import secrets
import time
from typing import Literal

from pydantic import Field
from sqlalchemy import delete, select

from omnitest.core.ir import Model
from omnitest.db import Member, Resource, Team, TeamMember

ROLE_RANK = {"viewer": 0, "operator": 1, "editor": 2}


class MemberInput(Model):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    display_name: str = Field(default="", max_length=200)
    enabled: bool = True
    is_admin: bool = False
    project_roles: dict[str, Literal["viewer", "operator", "editor"]] = Field(
        default_factory=dict, max_length=500
    )


class TeamInput(Model):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    enabled: bool = True
    project_roles: dict[str, Literal["viewer", "operator", "editor"]] = Field(
        default_factory=dict, max_length=500
    )
    member_ids: list[str] = Field(default_factory=list, max_length=500)


def digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def member_dict(row):
    return {
        key: getattr(row, key)
        for key in [
            "id",
            "username",
            "display_name",
            "enabled",
            "is_admin",
            "project_roles",
            "created_at",
            "rotated_at",
        ]
    }


def team_dict(row, session):
    return {
        **{
            key: getattr(row, key)
            for key in ["id", "name", "description", "enabled", "project_roles"]
        },
        "member_ids": list(
            session.scalars(select(TeamMember.member_id).where(TeamMember.team_id == row.id))
        ),
    }


class Members:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def require_bootstrap(self):
        if not self.settings.authentication:
            raise ValueError(
                "Configure administrator API credentials before enabling member accounts"
            )

    def startup(self):
        with self.db.session() as session:
            if not self.settings.authentication and (
                session.scalar(select(Member.id).limit(1))
                or session.scalar(select(Team.id).limit(1))
            ):
                raise ValueError(
                    "Member accounts exist; administrator authentication must remain configured"
                )

    @staticmethod
    def validate_projects(session, roles):
        for identity in roles:
            project = session.get(Resource, identity)
            if not project or project.kind != "projects":
                raise ValueError("Project grant references an unknown project")

    def listing(self):
        with self.db.session() as session:
            return {
                "authentication": self.settings.authentication,
                "members": [
                    member_dict(row)
                    for row in session.scalars(select(Member).order_by(Member.username))
                ],
                "teams": [
                    team_dict(row, session)
                    for row in session.scalars(select(Team).order_by(Team.name))
                ],
            }

    def save_member(self, value, identity=None):
        self.require_bootstrap()
        username = value.username.casefold()
        with self.db.session.begin() as session:
            self.validate_projects(session, value.project_roles)
            duplicate = session.scalar(select(Member).where(Member.username == username))
            if duplicate and duplicate.id != identity:
                raise ValueError("Member username already exists")
            token = None
            if identity:
                row = session.get(Member, identity)
                if not row:
                    raise LookupError("Member not found")
            else:
                token = "omt_" + secrets.token_urlsafe(32)
                row = Member(token_digest=digest(token))
                session.add(row)
            for key, val in value.model_dump().items():
                setattr(row, key, username if key == "username" else val)
            if not row.display_name:
                row.display_name = username
            session.flush()
            return {"member": member_dict(row), **({"token": token} if token else {})}

    def rotate(self, identity):
        self.require_bootstrap()
        token = "omt_" + secrets.token_urlsafe(32)
        with self.db.session.begin() as session:
            row = session.get(Member, identity)
            if not row:
                raise LookupError("Member not found")
            row.token_digest, row.rotated_at = digest(token), time.time()
            return {"member": member_dict(row), "token": token}

    def save_team(self, value, identity=None):
        self.require_bootstrap()
        with self.db.session.begin() as session:
            self.validate_projects(session, value.project_roles)
            duplicate = session.scalar(select(Team).where(Team.name == value.name))
            if duplicate and duplicate.id != identity:
                raise ValueError("Team name already exists")
            if len(set(value.member_ids)) != len(value.member_ids):
                raise ValueError("Team members must be unique")
            for member_id in value.member_ids:
                if not session.get(Member, member_id):
                    raise ValueError("Team references an unknown member")
            row = session.get(Team, identity) if identity else Team()
            if row is None:
                raise LookupError("Team not found")
            for key, val in value.model_dump(exclude={"member_ids"}).items():
                setattr(row, key, val)
            session.add(row)
            session.flush()
            session.execute(delete(TeamMember).where(TeamMember.team_id == row.id))
            for member_id in value.member_ids:
                session.add(TeamMember(team_id=row.id, member_id=member_id))
            session.flush()
            return team_dict(row, session)

    def delete_team(self, identity):
        self.require_bootstrap()
        with self.db.session.begin() as session:
            row = session.get(Team, identity)
            if not row:
                raise LookupError("Team not found")
            session.delete(row)

    def authenticate(self, token):
        from omnitest.services.access import Principal

        with self.db.session() as session:
            row = session.scalar(
                select(Member).where(Member.token_digest == digest(token), Member.enabled.is_(True))
            )
            if not row:
                return None
            roles = dict(row.project_roles)
            for team in session.scalars(
                select(Team)
                .join(TeamMember, Team.id == TeamMember.team_id)
                .where(TeamMember.member_id == row.id, Team.enabled.is_(True))
            ):
                for project, role in team.project_roles.items():
                    if ROLE_RANK[role] > ROLE_RANK.get(roles.get(project), -1):
                        roles[project] = role
            if len(roles) > 500:
                raise ValueError("Member effective project scope exceeds 500 projects")
            return Principal(
                "user:" + row.username,
                "admin" if row.is_admin else "member",
                frozenset(roles),
                roles,
                row.id,
                row.display_name,
            )


def mount(app, members):
    @app.get("/api/collaboration")
    def listing():
        return members.listing()

    @app.post("/api/collaboration/members", status_code=201)
    def create(value: MemberInput):
        return members.save_member(value)

    @app.put("/api/collaboration/members/{identity}")
    def update(identity: str, value: MemberInput):
        return members.save_member(value, identity)

    @app.post("/api/collaboration/members/{identity}/rotate")
    def rotate(identity: str):
        return members.rotate(identity)

    @app.post("/api/collaboration/teams", status_code=201)
    def create_team(value: TeamInput):
        return members.save_team(value)

    @app.put("/api/collaboration/teams/{identity}")
    def update_team(identity: str, value: TeamInput):
        return members.save_team(value, identity)

    @app.delete("/api/collaboration/teams/{identity}", status_code=204)
    def delete_team(identity: str):
        members.delete_team(identity)
