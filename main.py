import sys
from rich.console import Console
from rich.prompt import Prompt
from scraper import run_search
from output_writer import save_results

console = Console()

BANNER = """
linkedin]"""


def main():
    console.print(BANNER)

    # Allow query from CLI arg or interactive prompt
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        console.print(f"[dim]Using query from args:[/dim] {query}")
    else:
        query = Prompt.ask(
            "[bold yellow]Enter search query[/bold yellow]",
            default="Procurement manager Tata AutoComp Pune Linkedin",
        )

    if not query.strip():
        console.print("[red]Empty query. Exiting.[/red]")
        sys.exit(1)

    profiles = run_search(query.strip())

    if not profiles:
        console.print("\n[red]No LinkedIn profiles found. Try a different query.[/red]")
        sys.exit(0)

    save_results(profiles, query.strip())
    console.print(f"\n[bold green]Done![/bold green] Collected {len(profiles)} profile(s).")


if __name__ == "__main__":
    main()
