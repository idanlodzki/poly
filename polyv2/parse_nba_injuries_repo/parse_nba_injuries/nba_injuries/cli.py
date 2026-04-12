from __future__ import annotations

import json
import sys
from datetime import datetime

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from rich import box

from nba_injuries.fetcher import fetch_report, ET_OFFSET
from nba_injuries.models import InjuryReport, ReportChange
from nba_injuries.poller import poll, diff_reports

console = Console()

STATUS_COLORS = {
    "Out": "red",
    "Doubtful": "dark_orange",
    "Questionable": "yellow",
    "Probable": "green",
    "Available": "bright_green",
}


def _render_report(report: InjuryReport):
    console.print()
    ts = report.report_timestamp.strftime("%m/%d/%Y %I:%M %p ET")
    console.rule(f"[bold]NBA Injury Report - {ts}[/bold]")
    console.print(f"[dim]{len(report.records)} players across {len(report.by_team)} teams[/dim]")
    console.print()

    for team, players in report.by_team.items():
        table = Table(
            title=f"[bold]{team}[/bold]",
            box=box.ROUNDED,
            show_lines=False,
            title_justify="left",
            padding=(0, 1),
        )
        table.add_column("Player", style="white bold", min_width=22, no_wrap=True)
        table.add_column("Status", min_width=13)
        table.add_column("Matchup", style="cyan", min_width=8)
        table.add_column("Reason", min_width=30)

        for p in players:
            color = STATUS_COLORS.get(p.status, "white")
            table.add_row(
                p.player_name,
                f"[{color}]{p.status}[/{color}]",
                p.matchup,
                p.reason,
            )
        console.print(table)
        console.print()


def _render_changes(report: InjuryReport, changes: ReportChange):
    ts = report.report_timestamp.strftime("%I:%M %p ET")

    console.print()
    console.rule(f"[bold yellow]Changes at {ts} ({changes.summary_count} total)[/bold yellow]")

    if changes.new_injuries:
        console.print(f"\n[bold green]+ {len(changes.new_injuries)} New:[/bold green]")
        for r in changes.new_injuries:
            color = STATUS_COLORS.get(r.status, "white")
            console.print(
                f"  [green]+[/green] [bold]{r.player_name}[/bold] "
                f"([{color}]{r.status}[/{color}]) - {r.team}"
            )
            if r.reason:
                console.print(f"    [dim]{r.reason}[/dim]")

    if changes.removed_injuries:
        console.print(f"\n[bold red]- {len(changes.removed_injuries)} Removed:[/bold red]")
        for r in changes.removed_injuries:
            console.print(f"  [red]-[/red] [bold]{r.player_name}[/bold] - {r.team}")

    if changes.status_changes:
        console.print(f"\n[bold yellow]~ {len(changes.status_changes)} Status Changes:[/bold yellow]")
        for c in changes.status_changes:
            old_c = STATUS_COLORS.get(c["old_status"], "white")
            new_c = STATUS_COLORS.get(c["new_status"], "white")
            console.print(
                f"  [yellow]~[/yellow] [bold]{c['player']}[/bold] ({c['team']}): "
                f"[{old_c}]{c['old_status']}[/{old_c}] -> [{new_c}]{c['new_status']}[/{new_c}]"
            )

    if changes.reason_changes:
        console.print(f"\n[bold blue]i {len(changes.reason_changes)} Reason Updates:[/bold blue]")
        for c in changes.reason_changes:
            console.print(
                f"  [blue]i[/blue] [bold]{c['player']}[/bold] ({c['team']}): "
                f"[dim]{c['old_reason']}[/dim] -> {c['new_reason']}"
            )

    console.print()


@click.group()
def cli():
    """NBA Injury Report Tracker - real-time injury monitoring."""
    pass


@cli.command()
@click.option("--format", "fmt", type=click.Choice(["table", "json", "csv"]), default="table")
def latest(fmt: str):
    """Fetch and display the latest injury report."""
    with console.status("[bold green]Fetching latest report..."):
        try:
            report = fetch_report()
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            sys.exit(1)

    _output(report, fmt)


@cli.command()
@click.option("--date", "date_str", required=True, help="YYYY-MM-DD")
@click.option("--time", "time_str", required=True, help="HH:MM (24h ET)")
@click.option("--format", "fmt", type=click.Choice(["table", "json", "csv"]), default="table")
def fetch(date_str: str, time_str: str, fmt: str):
    """Fetch a specific injury report by date and time."""
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        console.print("[red]Invalid format. Use --date YYYY-MM-DD --time HH:MM[/red]")
        sys.exit(1)

    with console.status(f"[bold green]Fetching report for {date_str} {time_str} ET..."):
        try:
            report = fetch_report(dt)
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            sys.exit(1)

    _output(report, fmt)


@cli.command(name="watch")
def watch_cmd():
    """Watch for injury report changes in real-time (polls every 15 min)."""
    console.print(Panel(
        "[bold]NBA Injury Report Watcher[/bold]\n"
        "Polling every 15 minutes for changes.\n"
        "Press Ctrl+C to stop.",
        border_style="cyan",
    ))

    first_run = True

    def on_update(report: InjuryReport, changes: ReportChange):
        nonlocal first_run
        if first_run:
            console.print(
                f"[bold green]Initial report loaded:[/bold green] "
                f"{len(report.records)} injuries across {len(report.by_team)} teams"
            )
            _render_report(report)
            first_run = False
        else:
            _render_changes(report, changes)

    def on_no_change(dt: datetime):
        ts = dt.strftime("%I:%M %p")
        console.print(f"[dim][{ts}] No changes.[/dim]")

    def on_error(e: Exception):
        console.print(f"[red]Error: {e}[/red]")

    try:
        poll(on_update=on_update, on_error=on_error, on_no_change=on_no_change)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped watching.[/yellow]")


@cli.command()
@click.option("--date", "date_str", default=None, help="YYYY-MM-DD (defaults to today)")
@click.option("--time", "time_str", default=None, help="HH:MM (24h ET, defaults to now)")
@click.option("--team", "team_filter", default=None, help="Filter by team (partial match)")
@click.option("--player", "player_filter", default=None, help="Filter by player (partial match)")
@click.option("--status", "status_filter", default=None, help="Filter by status (Out, Questionable, etc.)")
@click.option("--format", "fmt", type=click.Choice(["table", "json", "csv"]), default="table")
def search(date_str: str | None, time_str: str | None, team_filter: str | None,
           player_filter: str | None, status_filter: str | None, fmt: str):
    """Search injuries by team, player, or status."""
    now = datetime.now(ET_OFFSET)
    if date_str and time_str:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    elif date_str:
        dt = datetime.strptime(f"{date_str} {now.strftime('%H:%M')}", "%Y-%m-%d %H:%M")
    else:
        dt = now

    with console.status("[bold green]Fetching report..."):
        try:
            report = fetch_report(dt)
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            sys.exit(1)

    filtered = report.records
    if team_filter:
        filtered = [r for r in filtered if team_filter.lower() in r.team.lower()]
    if player_filter:
        filtered = [r for r in filtered if player_filter.lower() in r.player_name.lower()]
    if status_filter:
        filtered = [r for r in filtered if status_filter.lower() == r.status.lower()]

    report = report.model_copy(update={"records": filtered})
    console.print(f"[dim]Found {len(filtered)} matching records[/dim]")
    _output(report, fmt)


@cli.command()
@click.option("--date1", required=True, help="First report: YYYY-MM-DD HH:MM")
@click.option("--date2", required=True, help="Second report: YYYY-MM-DD HH:MM")
def diff(date1: str, date2: str):
    """Compare two reports and show differences."""
    try:
        dt1 = datetime.strptime(date1, "%Y-%m-%d %H:%M")
        dt2 = datetime.strptime(date2, "%Y-%m-%d %H:%M")
    except ValueError:
        console.print("[red]Use format: YYYY-MM-DD HH:MM[/red]")
        sys.exit(1)

    with console.status("[bold green]Fetching reports..."):
        r1 = fetch_report(dt1)
        r2 = fetch_report(dt2)

    changes = diff_reports(r1, r2)
    if changes.has_changes:
        _render_changes(r2, changes)
    else:
        console.print("[green]No differences found.[/green]")


def _output(report: InjuryReport, fmt: str):
    if fmt == "table":
        _render_report(report)
    elif fmt == "json":
        data = [r.model_dump() for r in report.records]
        console.print_json(json.dumps(data, indent=2, default=str))
    elif fmt == "csv":
        import pandas as pd
        df = pd.DataFrame([r.model_dump() for r in report.records])
        click.echo(df.to_csv(index=False))


def main():
    cli()


if __name__ == "__main__":
    main()
