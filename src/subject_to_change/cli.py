from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from .config import AppPaths, Settings, canonical_folders
from .embeddings import MODEL, download_model, model_ready
from .engine import Engine


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(
        description="Search chosen folders locally, by meaning and words."
    )
    app.add_argument("--data-dir", type=Path, help="Override the local index and model directory")
    commands = app.add_subparsers(dest="command")
    commands.add_parser("gui", help="Open the desktop app (also the default)")
    commands.add_parser(
        "status", help="Show local model and index status; never downloads anything"
    )
    commands.add_parser("download-model", help="Explicitly download the embedding model")
    index = commands.add_parser("index", help="Index folders, or the folders saved in the app")
    index.add_argument("folders", nargs="*", type=Path)
    index.add_argument("--keyword-only", action="store_true", help="Index without loading a model")
    search = commands.add_parser("search", help="Search the local index")
    search.add_argument("query")
    search.add_argument(
        "--keyword-only", action="store_true", help="Search without loading a model"
    )
    search.add_argument("--limit", type=int, default=20, choices=range(1, 101), metavar="1-100")
    return app


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    paths = AppPaths(args.data_dir.expanduser().resolve()) if args.data_dir else AppPaths.default()
    try:
        if args.command in {None, "gui"}:
            from .ui import main as gui

            return gui(paths)
        engine = Engine(paths)
        if args.command == "status":
            print(
                json.dumps(
                    {
                        "data": str(paths.data),
                        "model": MODEL.repository,
                        "model_ready": model_ready(paths.model),
                        **engine.stats(),
                    },
                    indent=2,
                )
            )
        elif args.command == "download-model":
            download_model(paths.model, lambda message: print(message, file=sys.stderr))
        elif args.command == "index":
            settings = Settings.load(paths.settings)
            roots = canonical_folders(args.folders or settings.folders)
            # The CLI's selected folders become the app's selected folders too.
            if roots:
                settings.folders = [str(root) for root in roots]
                settings.save(paths.settings)
                engine.retain_folders(settings.folders)
            report = engine.index(
                roots,
                semantic=not args.keyword_only,
                progress=lambda message: print(message, file=sys.stderr),
            )
            print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
            return 2 if report.errors else 0
        elif args.command == "search":
            results = engine.search(args.query, semantic=not args.keyword_only, limit=args.limit)
            print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        print("Stopped. Completed files remain indexed.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, ImportError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
