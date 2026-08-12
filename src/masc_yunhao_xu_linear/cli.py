"""Command-line interface for masc-yunhao-xu-linear."""

import json
from typing import Annotated

import typer
from rich.console import Console

from masc_yunhao_xu_linear import __version__

app = typer.Typer(
    add_completion=False,
    help="Linear principal-agent salvage-subsidy pipeline for TSA29 Williams Lake timber supply.",
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"masc-yunhao-xu-linear {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            help="Show the package version and exit.",
            is_eager=True,
        ),
    ] = False,
) -> None:
    """masc-yunhao-xu-linear command-line interface."""


@app.command()
def ingest(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit deterministic JSON output."),
    ] = False,
) -> None:
    """Ingest predecessor data sources into the model input layer."""
    _stub_exit("ingest", json_output)


@app.command(name="ws3-run")
def ws3_run(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit deterministic JSON output."),
    ] = False,
) -> None:
    """Compile a full-TSA WS3 schedule for the linear pipeline."""
    _stub_exit("ws3-run", json_output)


@app.command(name="solve-principal")
def solve_principal(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit deterministic JSON output."),
    ] = False,
) -> None:
    """Solve the principal-side linear HiGHS LP."""
    _stub_exit("solve-principal", json_output)


@app.command(name="solve-agent")
def solve_agent(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit deterministic JSON output."),
    ] = False,
) -> None:
    """Solve the agent-side linear HiGHS LP."""
    _stub_exit("solve-agent", json_output)


@app.command(name="rh-run")
def rh_run(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit deterministic JSON output."),
    ] = False,
) -> None:
    """Run the rolling-horizon principal-agent coordination loop."""
    _stub_exit("rh-run", json_output)


@app.command()
def export(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit deterministic JSON output."),
    ] = False,
) -> None:
    """Export pipeline results to tabular artifacts."""
    _stub_exit("export", json_output)


def _stub_exit(command: str, json_output: bool) -> None:
    """Fail fast with a diagnostic because the command is scaffolded only."""
    message = f"'{command}' is not implemented yet; phase 1 ships stubs only."
    if json_output:
        payload = {
            "ok": False,
            "command": command,
            "diagnostic": message,
        }
        console.out(json.dumps(payload, indent=2, sort_keys=True))
    else:
        console.print(f"Error: {message}")
    raise typer.Exit(code=1)
