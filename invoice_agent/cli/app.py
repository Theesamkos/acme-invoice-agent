"""CLI entry point: argument parsing and top-level orchestration."""

from __future__ import annotations

import argparse

from rich.console import Console

console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Process invoices: ingestion -> validation -> approval -> payment.",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--invoice_path", help="Path to a single invoice file to process")
    target.add_argument(
        "--batch", action="store_true", help="Process every invoice in data/invoices/"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Show full agent traces and LLM reasoning"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console.print(
        f"[yellow]Pipeline not wired up yet[/] -- scaffolding stage. Parsed arguments: {vars(args)}"
    )
    return 0
