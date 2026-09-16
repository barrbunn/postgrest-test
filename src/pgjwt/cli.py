"""pgjwt: generate HS256 JWTs for PostgREST.

PostgREST reads the database role from the JWT `role` claim and verifies the
signature against PGRST_JWT_SECRET.
"""

import json
import os

import typer

from pgjwt import token

app = typer.Typer(
    name="pgjwt",
    help="Generate JWT tokens for PostgREST (HS256, secret from PGRST_JWT_SECRET).",
    no_args_is_help=True,
)


@app.callback(invoke_without_command=True)
def generate(
    role: str = typer.Option(..., "--role", "-r", help="Database role to put in the JWT (e.g. editor, anon)"),
    exp: str = typer.Option(None, "--exp", "-e", help="Validity duration, e.g. 1h, 30m, 2d"),
    claim: list[str] = typer.Option(None, "--claim", "-c", help="Extra claim as key=value (repeatable)"),
    curl: bool = typer.Option(False, "--curl", help="Also print a ready-to-use curl command"),
) -> None:
    """Generate a JWT for PostgREST and print it to stdout."""
    claims: dict = {}
    for item in claim or []:
        if "=" not in item:
            raise typer.BadParameter(f"claim must be key=value: {item!r}")
        key, value = item.split("=", 1)
        try:
            claims[key] = json.loads(value)
        except json.JSONDecodeError:
            claims[key] = value

    try:
        jwt = token.encode(role, exp=exp, claims=claims)
    except ValueError as ex:
        raise typer.BadParameter(str(ex))
    except RuntimeError as ex:
        typer.echo(f"error: {ex}", err=True)
        raise typer.Exit(code=1)

    typer.echo(jwt)
    if curl:
        typer.echo()
        typer.echo(f'curl -H "Authorization: Bearer {jwt}" http://postgrest:3000/todos')
