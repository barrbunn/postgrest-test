"""Pydantic models for the yaml templates, plus loaders."""

from pathlib import Path
from typing import Literal, Union

import typer
import yaml
from pydantic import BaseModel, Field

from provision import config

Permission = Literal["select", "insert", "update", "delete"]
SqlLiteral = Union[str, int, float, bool, None]


class Column(BaseModel):
    name: str
    type: str
    primary_key: bool = False
    not_null: bool = False
    default: SqlLiteral = None


class Table(BaseModel):
    name: str
    columns: list[Column] = Field(min_length=1)


class TablesDoc(BaseModel):
    tables: list[Table]


class User(BaseModel):
    name: str
    password: str | None = None
    login: bool = True
    grant_to: list[str] = []


class UsersDoc(BaseModel):
    users: list[User]


class Role(BaseModel):
    name: str
    login: bool = False
    grant_to: list[str] = []


class RolesDoc(BaseModel):
    roles: list[Role]


class Grant(BaseModel):
    user: str
    tables: list[str] = Field(min_length=1)
    permissions: list[Permission] = Field(min_length=1)


class GrantsDoc(BaseModel):
    grants: list[Grant]


class Function(BaseModel):
    name: str
    returns: str = "void"
    language: str = "plpgsql"
    args: str = ""
    body: str


class FunctionsDoc(BaseModel):
    functions: list[Function]


class PostgrestConfig(BaseModel):
    role: str = "authenticator"
    settings: dict[str, str]


class PostgrestConfigDoc(BaseModel):
    postgrest: PostgrestConfig


class AccessFilterRow(BaseModel):
    role: str
    table: str
    visible_columns: list[str]


class AccessFilterDoc(BaseModel):
    access_filter: list[AccessFilterRow]


def _load(path: Path) -> dict:
    if not path.exists():
        typer.echo(f"error: {path} not found", err=True)
        raise typer.Exit(code=1)
    with path.open() as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        typer.echo(f"error: {path} must contain a mapping", err=True)
        raise typer.Exit(code=1)
    return data


def load_tables() -> TablesDoc:
    return TablesDoc.model_validate(_load(config.tables_file()))


def load_users() -> UsersDoc:
    return UsersDoc.model_validate(_load(config.users_file()))


def load_roles() -> RolesDoc:
    return RolesDoc.model_validate(_load(config.roles_file()))


def load_grants() -> GrantsDoc:
    return GrantsDoc.model_validate(_load(config.grants_file()))


def load_functions() -> FunctionsDoc:
    return FunctionsDoc.model_validate(_load(config.functions_file()))


def load_postgrest() -> PostgrestConfigDoc:
    return PostgrestConfigDoc.model_validate(_load(config.postgrest_file()))


def load_access_filter() -> AccessFilterDoc:
    return AccessFilterDoc.model_validate(_load(config.access_filter_file()))
