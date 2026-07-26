"""
worksummary — Scan git repos and generate an AI-powered work summary.

Walks a root directory (and subdirectories) to discover all Git repos,
fetches latest code, collects commits authored by you within a date range,
then sends that commit list to Google Gemini to produce a concise work summary.

Your Git username/email can differ across repos — the script reads each
repo's configured user.name and user.email to match commits correctly.

Requires:
    pipx install -e ".[ai]"   (installs google-genai and keyring)

Usage:
    worksummary                        # interactive date prompt
    worksummary -s 2026-02-01          # explicit start date
    worksummary -s 2026-02-01 -u 2026-02-28
    worksummary -F                     # skip 'git fetch'
    worksummary -k                     # store your Gemini API key and exit
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.markdown import Markdown

from toolboxcli._common.console import console, die
from toolboxcli._common.gitrepo import discover_repos, git
from toolboxcli._common.progress import spinner
from toolboxcli._common.secrets import get_secret

try:
    from google import genai
except ImportError:
    genai = None

try:
    import keyring
except ImportError:
    keyring = None

KEYRING_SERVICE = "worksummary"
KEYRING_USERNAME = "gemini-api-key"


def _require_ai_deps() -> None:
    missing = []
    if genai is None:
        missing.append("google-genai")
    if keyring is None:
        missing.append("keyring")
    if missing:
        die(
            f"missing required package(s): {', '.join(missing)}. "
            'Install with:  pipx inject shubhs-toolbox ' + " ".join(missing)
        )


# ── Git helpers ───────────────────────────────────────────────────────

def get_git_user(repo: Path) -> tuple[str, str]:
    """Return (user.name, user.email) configured for *repo*."""
    name = git("config", "user.name", cwd=repo).stdout.strip()
    email = git("config", "user.email", cwd=repo).stdout.strip()
    return name, email


def fetch_repo(repo: Path) -> bool:
    """Run 'git fetch --all' in *repo*. Returns True on success."""
    result = git("fetch", "--all", "--quiet", cwd=repo)
    return result.returncode == 0


def get_commits(repo: Path, author: str, since: str, until: str) -> list[dict]:
    """Return a list of commit dicts for *author* in the given date range.

    Each dict has keys: hash, date, subject, body.
    """
    # Use %x00 (null byte) as field separator and %x01 as record separator
    fmt = "%H%x00%ai%x00%s%x00%b%x01"
    result = git(
        "log", "--all",
        f"--author={author}",
        f"--since={since}",
        f"--until={until}",
        f"--pretty=format:{fmt}",
        "--no-merges",
        cwd=repo,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    commits = []
    for record in result.stdout.split("\x01"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x00")
        if len(parts) < 3:
            continue
        commits.append({
            "hash": parts[0][:8],
            "date": parts[1],
            "subject": parts[2],
            "body": parts[3].strip() if len(parts) > 3 else "",
        })
    return commits


# ── Date helpers ──────────────────────────────────────────────────────

def parse_date(s: str) -> datetime:
    """Parse a YYYY-MM-DD string into a datetime."""
    return datetime.strptime(s, "%Y-%m-%d")


def prompt_dates() -> tuple[str, str]:
    """Interactively ask the user for start and end dates."""
    console.print()
    today = datetime.now()
    week_ago = today - timedelta(days=7)

    console.print(
        f"[dim]Today is {today.strftime('%Y-%m-%d')}. "
        f"Default range: last 7 days.[/dim]"
    )

    since_str = console.input(
        f"[bold cyan]Start date[/bold cyan] "
        f"[dim](YYYY-MM-DD, default {week_ago.strftime('%Y-%m-%d')})[/dim]: "
    ).strip()
    if not since_str:
        since_str = week_ago.strftime("%Y-%m-%d")

    until_str = console.input(
        f"[bold cyan]End date[/bold cyan]   "
        f"[dim](YYYY-MM-DD, default {today.strftime('%Y-%m-%d')})[/dim]: "
    ).strip()
    if not until_str:
        until_str = today.strftime("%Y-%m-%d")

    try:
        parse_date(since_str)
        parse_date(until_str)
    except ValueError:
        die("Invalid date format. Use YYYY-MM-DD.")

    return since_str, until_str


# ── Gemini summary ───────────────────────────────────────────────────

def generate_summary(all_commits: dict[str, list[dict]], since: str, until: str) -> str:
    """Send commit data to Gemini and return a Markdown work summary.

    *all_commits* maps repo name → list of commit dicts.
    """
    api_key = get_secret(KEYRING_SERVICE, KEYRING_USERNAME, env_var="GEMINI_API_KEY")

    if not api_key:
        die(
            "Gemini API key not found. Store it securely (recommended): "
            "worksummary --set-key\n"
            "Or export as an environment variable:  export GEMINI_API_KEY='your-key'"
        )

    client = genai.Client(api_key=api_key)

    lines = []
    for repo, commits in all_commits.items():
        lines.append(f"\n## Repository: {repo}")
        for c in commits:
            body_snippet = f"\n  {c['body']}" if c["body"] else ""
            lines.append(f"- [{c['date']}] {c['subject']}{body_snippet}")
    commit_text = "\n".join(lines)

    prompt = (
        "You are a helpful assistant that writes professional work summaries.\n\n"
        f"Below is a list of Git commits I made between {since} and {until}, "
        "organized by repository. Generate a clear, concise **work summary** "
        "in Markdown. Group related work thematically (not by repo unless it "
        "makes sense). Highlight key accomplishments, features added, bugs "
        "fixed, and refactors. Keep it professional and suitable for sharing "
        "with a manager or team. Exclude commit hashes.\n\n"
        "--- BEGIN COMMITS ---\n"
        f"{commit_text}\n"
        "--- END COMMITS ---"
    )

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
    )
    return response.text


# ── Main ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="worksummary",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("root", nargs="?", default=".", help="Root directory to scan for repos (default: .)")
    parser.add_argument("-s", "--since", metavar="DATE", help="Start date (YYYY-MM-DD). Prompted if omitted.")
    parser.add_argument("-u", "--until", metavar="DATE", help="End date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("-F", "--no-fetch", action="store_true", help="Skip 'git fetch' in each repo.")
    parser.add_argument(
        "-k", "--set-key", action="store_true",
        help="Store Gemini API key in the OS credential store and exit.",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.set_key:
        _require_ai_deps()
        key = getpass.getpass("Enter your Gemini API key: ")
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key)
        console.print("[bold green]API key saved to OS credential store.[/bold green]")
        sys.exit(0)

    _require_ai_deps()

    if args.since:
        since = args.since
        until = args.until or datetime.now().strftime("%Y-%m-%d")
        try:
            parse_date(since)
            parse_date(until)
        except ValueError:
            die("Invalid date format. Use YYYY-MM-DD.")
    else:
        since, until = prompt_dates()

    # Make until inclusive by adding one day for git log --until
    until_exclusive = (parse_date(until) + timedelta(days=1)).strftime("%Y-%m-%d")

    root = Path(args.root).resolve()
    console.print(
        Panel(
            f"[bold]Scanning:[/bold] {root}\n"
            f"[bold]Period:[/bold]   {since}  →  {until}",
            title="[bold cyan]worksummary[/bold cyan]",
            border_style="cyan",
        )
    )

    with spinner(transient=True) as progress:
        progress.add_task("Discovering git repos…", total=None)
        repos = discover_repos(root)

    if not repos:
        console.print("[yellow]No git repositories found.[/yellow]")
        sys.exit(0)

    console.print(f"Found [bold]{len(repos)}[/bold] git repo(s).\n")

    all_commits: dict[str, list[dict]] = {}
    total_commits = 0

    with spinner() as progress:
        task = progress.add_task("Processing repos…", total=len(repos))

        for repo in repos:
            repo_name = repo.relative_to(root)
            progress.update(task, description=f"[cyan]{repo_name}[/cyan]")

            if not args.no_fetch:
                fetch_repo(repo)

            name, email = get_git_user(repo)
            if not name and not email:
                progress.advance(task)
                continue

            author = email if email else name

            commits = get_commits(repo, author, since, until_exclusive)
            if commits:
                all_commits[str(repo_name)] = commits
                total_commits += len(commits)

            progress.advance(task)

    if not all_commits:
        console.print("[yellow]No commits found in the given date range.[/yellow]")
        sys.exit(0)

    table = Table(title="Commits Found", box=box.ROUNDED, border_style="dim")
    table.add_column("Repository", style="cyan")
    table.add_column("Commits", justify="right", style="green")
    for repo_name, commits in all_commits.items():
        table.add_row(repo_name, str(len(commits)))
    table.add_section()
    table.add_row("[bold]Total[/bold]", f"[bold]{total_commits}[/bold]")
    console.print(table)
    console.print()

    with spinner(transient=True) as progress:
        progress.add_task("Generating summary with Gemini…", total=None)
        summary = generate_summary(all_commits, since, until)

    console.print(Panel(
        Markdown(summary),
        title="[bold green]Work Summary[/bold green]",
        border_style="green",
        padding=(1, 2),
    ))


if __name__ == "__main__":
    main()
