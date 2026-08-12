"""Command-line interface for fresh-salvage."""

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from fresh_salvage import __version__, data, ws3
from fresh_salvage.models import Diagnostic, IngestResult, ScenarioRunConfig, WS3Result

app = typer.Typer(
    add_completion=False,
    help="Linear principal-agent salvage-subsidy pipeline for TSA29 Williams Lake timber supply.",
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"fresh-salvage {__version__}")
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
    """fresh-salvage command-line interface."""


@app.command()
def ingest(
    scenario_path: Annotated[
        Path,
        typer.Argument(help="Path to a scenario YAML or JSON config."),
    ],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit deterministic JSON output."),
    ] = False,
) -> None:
    """Ingest predecessor data sources into the model input layer."""
    try:
        scenario = ScenarioRunConfig.read(scenario_path)
        result = data.ingest(scenario)
    except Exception as exc:
        diagnostic = Diagnostic(
            severity="error",
            code="ingest_failed",
            message=str(exc),
            context={
                "scenario_path": str(scenario_path),
                "exception_type": type(exc).__name__,
            },
        )
        _print_failure(diagnostic, json_output, command="ingest")
        raise typer.Exit(code=1)
    _print_ingest_summary(result, json_output)


@app.command(name="ws3-run")
def ws3_run(
    config_path: Annotated[
        Path,
        typer.Argument(help="Path to a WS3 run YAML or JSON config."),
    ],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit deterministic JSON output."),
    ] = False,
    smoke: Annotated[
        bool,
        typer.Option("--smoke", help="Run the deterministic 3-period smoke profile."),
    ] = False,
) -> None:
    """Compile and solve a full-TSA WS3 schedule for the linear pipeline."""
    try:
        config = ws3.WS3RunConfig.read(config_path)
        if smoke:
            output_root = Path("outputs/ws3_smoke")
            bridge = ws3.resolved_bridge_path(config)
            config = ws3.smoke_config(bridge, output_root)
        result = ws3.run_ws3(config)
    except Exception as exc:
        diagnostic = Diagnostic(
            severity="error",
            code=getattr(exc, "code", "ws3_run_failed"),
            message=str(exc),
            context={
                "config_path": str(config_path),
                "smoke": smoke,
                "exception_type": type(exc).__name__,
            },
        )
        _print_failure(diagnostic, json_output, command="ws3-run")
        raise typer.Exit(code=1)
    _print_ws3_summary(result, json_output)


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


def _print_ingest_summary(result: IngestResult, json_output: bool) -> None:
    """Print the ingestion summary as JSON or a Rich report."""

    if json_output:
        payload = {"ok": True, "command": "ingest", **result.summary()}
        console.out(json.dumps(payload, indent=2, sort_keys=True))
        return

    console.print(f"[bold green]Ingest complete:[/bold green] {result.run_id}")
    console.print(f"  Total stands: {result.total_stands:,}")
    console.print(f"  Burned stands: {result.burned_stands:,}")
    console.print(f"  Green volume: {result.green_volume:,.0f} m3")
    console.print(f"  Burned volume: {result.burned_volume:,.0f} m3")
    console.print(f"  Duration: {result.duration_seconds:.1f} s")

    zone_table = Table(title="Stands per BEC zone")
    zone_table.add_column("BEC zone")
    zone_table.add_column("Stands", justify="right")
    for zone, count in sorted(result.per_bec_zone_counts.items()):
        zone_table.add_row(zone, f"{count:,}")
    console.print(zone_table)

    for diagnostic in result.diagnostics:
        console.print(
            f"[yellow]Warning:[/yellow] {diagnostic.code}: {diagnostic.message}"
        )

    console.print("Artifacts:")
    console.print(f"  {result.data_path}")
    console.print(f"  {result.csv_path}")
    console.print(f"  {result.manifest_path}")


def _print_ws3_summary(result: WS3Result, json_output: bool) -> None:
    """Print the WS3 solve summary as JSON or a Rich report."""

    if json_output:
        payload = {"ok": True, "command": "ws3-run", **result.summary()}
        console.out(json.dumps(payload, indent=2, sort_keys=True))
        return

    console.print(f"[bold green]WS3 solve complete:[/bold green] {result.run_id}")
    console.print(f"  Status: {result.status}")
    console.print(f"  Periods: {result.periods}")
    console.print(f"  Schedule rows: {result.schedule_row_counts.get('total', 0):,}")
    console.print(f"  Objective value: {result.objective_value:,.0f} m3")
    console.print(f"  Solve time: {result.solve_seconds:.1f} s")

    period_table = Table(title="Harvest volume per period")
    period_table.add_column("Period")
    period_table.add_column("Volume (M m3/yr)", justify="right")
    period_table.add_column("Area (ha)", justify="right")
    for period, volume in sorted(result.per_period_volumes_m3.items()):
        area = result.per_period_area_ha.get(period, 0.0)
        volume_per_year = volume / result.period_length / 1e6
        period_table.add_row(period, f"{volume_per_year:.2f}", f"{area:,.0f}")
    console.print(period_table)

    for diagnostic in result.diagnostics:
        console.print(
            f"[yellow]Warning:[/yellow] {diagnostic.code}: {diagnostic.message}"
        )

    console.print("Artifacts:")
    console.print(f"  {result.data_path}")
    console.print(f"  {result.csv_path}")
    console.print(f"  {result.manifest_path}")


def _print_failure(diagnostic: Diagnostic, json_output: bool, command: str) -> None:
    """Print a structured failure diagnostic."""

    if json_output:
        payload = {
            "ok": False,
            "command": command,
            "diagnostic": diagnostic.message,
            **diagnostic.model_dump(),
        }
        console.out(json.dumps(payload, indent=2, sort_keys=True))
    else:
        console.print(f"Error: {diagnostic.message}")


def _stub_exit(command: str, json_output: bool) -> None:
    """Fail fast with a diagnostic because the command is scaffolded only."""
    message = f"'{command}' is not implemented yet (see ROADMAP)."
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
