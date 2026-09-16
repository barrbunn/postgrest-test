"""pgmkcurl: build curl commands for PostgREST.

Usage examples:

    pgmkcurl get todos --query "select=title&done=eq.false"
    pgmkcurl --role editor post todos --data '{"id": 1, "title": "x", "done": false}'
    pgmkcurl --token <jwt> patch "todos?id=eq.1" --data '{"done": true}'
    pgmkcurl --role editor delete "todos?id=eq.1"
"""

import os
import shlex

import typer

from pgjwt import token as jwttoken

app = typer.Typer(
    name="pgmkcurl",
    help="Build curl commands for PostgREST (prints the command, does not run it).",
    no_args_is_help=True,
)


def _default_host() -> str:
    return os.environ.get("POSTGREST_URL", "http://postgrest:3000")


@app.callback()
def callback(
    ctx: typer.Context,
    host: str = typer.Option(None, "--host", help="PostgREST base URL (default: $POSTGREST_URL or http://postgrest:3000)"),
    token_value: str = typer.Option(None, "--token", "-t", help="Raw JWT to use"),
    role: str = typer.Option(None, "--role", "-r", help="Generate a JWT for this role via pgjwt"),
) -> None:
    if token_value and role:
        raise typer.BadParameter("--token and --role are mutually exclusive")
    ctx.obj = {
        "host": (host or _default_host()).rstrip("/"),
        "token": token_value,
        "role": role,
    }


def _build(ctx: typer.Context, method: str, path: str, query: str | None, data: str | None) -> None:
    opts = ctx.obj
    url = opts["host"] + (path if path.startswith("/") else "/" + path)
    if query:
        url += ("&" if "?" in url else "?") + query

    parts = ["curl", "-sS"]
    if method != "GET":
        parts += ["-X", method]
    jwt = opts["token"]
    if opts["role"]:
        try:
            jwt = jwttoken.encode(opts["role"])
        except RuntimeError as ex:
            typer.echo(f"error: {ex}", err=True)
            raise typer.Exit(code=1)
    if jwt:
        parts += ["-H", f"Authorization: Bearer {jwt}"]
    if data is not None:
        parts += ["-H", "Content-Type: application/json", "-d", data]
    parts.append(url)

    typer.echo(shlex.join(parts))


@app.command("get")
def get(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Resource path, e.g. todos or /todos?id=eq.1"),
    query: str = typer.Option(None, "--query", "-q", help="URL-encoded query string, e.g. select=title&done=eq.false"),
) -> None:
    """Build a GET command."""
    _build(ctx, "GET", path, query, None)


@app.command("post")
def post(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    query: str = typer.Option(None, "--query", "-q", help="URL-encoded query string"),
    data: str = typer.Option(None, "--data", "-d", help="JSON body, e.g. '{\"id\": 1, \"title\": \"x\"}'"),
) -> None:
    """Build a POST command."""
    _build(ctx, "POST", path, query, data)


@app.command("put")
def put(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    query: str = typer.Option(None, "--query", "-q", help="URL-encoded query string"),
    data: str = typer.Option(None, "--data", "-d", help="JSON body"),
) -> None:
    """Build a PUT command."""
    _build(ctx, "PUT", path, query, data)


@app.command("patch")
def patch(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    query: str = typer.Option(None, "--query", "-q", help="URL-encoded query string"),
    data: str = typer.Option(None, "--data", "-d", help="JSON body (partial update)"),
) -> None:
    """Build a PATCH command."""
    _build(ctx, "PATCH", path, query, data)


@app.command("delete")
def delete(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    query: str = typer.Option(None, "--query", "-q", help="URL-encoded query string, e.g. id=eq.1"),
) -> None:
    """Build a DELETE command."""
    _build(ctx, "DELETE", path, query, None)
