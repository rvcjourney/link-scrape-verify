import json
import csv
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.table import Table

console = Console()


def save_results(profiles: list[dict], query: str):
    """Save results to JSON, CSV, and print a rich table."""
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in query)[:50].strip()
    base = output_dir / f"{safe_name}_{timestamp}"

    # JSON
    json_path = base.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=2, ensure_ascii=False)

    # CSV
    csv_path = base.with_suffix(".csv")
    if profiles:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=profiles[0].keys())
            writer.writeheader()
            writer.writerows(profiles)

    # Rich table
    table = Table(title=f"LinkedIn Profiles Found ({len(profiles)})", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Name", style="bold white", min_width=20)
    table.add_column("Title", style="cyan", min_width=25)
    table.add_column("Company", style="green", min_width=20)
    table.add_column("LinkedIn URL", style="blue", min_width=40, no_wrap=False)

    for i, p in enumerate(profiles, 1):
        table.add_row(
            str(i),
            p.get("name") or "-",
            p.get("title") or "-",
            p.get("company") or "-",
            p.get("linkedin_url") or "-",
        )

    console.print(table)
    console.print(f"\n[bold green]Saved:[/bold green] {json_path}")
    console.print(f"[bold green]Saved:[/bold green] {csv_path}")
