"""
Reset the demo project to a clean pre-run state.

What this wipes:
  - data/events.ndjson          → emptied
  - data/warehouse.duckdb       → deleted
  - data/cache/llm/             → deleted (forces LLM re-parse on next run)
  - data/runs/*.json            → deleted
  - projects/<id>/events.ndjson → emptied (if project-local)
  - projects/<id>/raw/manifest.json       → reset to {}
  - projects/<id>/raw/parse_manifest.json → deleted
  - projects/<id>/raw/compile_manifest.json → deleted
  - kb/projects/<id>/            → deleted (the compiled wiki for this project)
  - projects/<id>/raw/emails/             → files moved back to _inbox/
  - projects/<id>/raw/docs/               → files moved back to _inbox/
  - projects/<id>/raw/backlog/            → files moved back to _inbox/

Usage:
  uv run python scripts/reset_demo.py
  uv run python scripts/reset_demo.py --project acme--platform-2026
  uv run python scripts/reset_demo.py --keep-cache   # skip LLM cache wipe (re-uses responses)
  uv run python scripts/reset_demo.py --yes           # skip confirmation prompt
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent


def _confirm(msg: str) -> bool:
    answer = input(f"{msg} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def reset(project_id: str, keep_cache: bool, yes: bool) -> None:
    project_dir = ROOT / "projects" / project_id
    data_dir = ROOT / "data"

    if not project_dir.exists():
        print(f"ERROR: project not found: {project_dir}", file=sys.stderr)
        sys.exit(1)

    raw_dir = project_dir / "raw"
    inbox_dir = raw_dir / "_inbox"
    typed_folders = ["emails", "docs", "backlog", "meetings"]

    # --- preview ---
    print(f"\nReset target: {project_id}")
    print(f"  data/events.ndjson          → empty")
    print(f"  data/warehouse.duckdb       → delete")
    if not keep_cache:
        print(f"  data/cache/llm/             → delete (next run re-parses via API)")
    else:
        print(f"  data/cache/llm/             → kept (--keep-cache)")
    print(f"  data/runs/                  → delete all run logs")
    print(f"  projects/{project_id}/raw/manifest.json       → {{}}")
    print(f"  projects/{project_id}/raw/parse_manifest.json → delete")
    for folder in typed_folders:
        p = raw_dir / folder
        if p.exists():
            files = list(p.iterdir())
            if files:
                print(f"  projects/{project_id}/raw/{folder}/  → {len(files)} file(s) moved to _inbox/")
    print()

    if not yes and not _confirm("Proceed with reset?"):
        print("Aborted.")
        return

    # --- events.ndjson (root data/) ---
    events_root = data_dir / "events.ndjson"
    if events_root.exists():
        events_root.write_text("", encoding="utf-8")
        print(f"  cleared: {events_root.relative_to(ROOT)}")

    # --- events.ndjson (project-local, if present) ---
    events_proj = project_dir / "events.ndjson"
    if events_proj.exists():
        events_proj.write_text("", encoding="utf-8")
        print(f"  cleared: {events_proj.relative_to(ROOT)}")

    # --- warehouse ---
    db = data_dir / "warehouse.duckdb"
    if db.exists():
        db.unlink()
        print(f"  deleted: {db.relative_to(ROOT)}")
    # DuckDB WAL files
    for wal in data_dir.glob("warehouse.duckdb*"):
        wal.unlink()

    # --- LLM cache ---
    if not keep_cache:
        cache_dir = data_dir / "cache" / "llm"
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            print(f"  deleted: {cache_dir.relative_to(ROOT)}/")

    # --- run logs ---
    runs_dir = data_dir / "runs"
    if runs_dir.exists():
        deleted = 0
        for f in runs_dir.glob("*.json"):
            f.unlink()
            deleted += 1
        if deleted:
            print(f"  deleted: {deleted} run log(s) in data/runs/")

    # --- ingest manifest → {} ---
    manifest = raw_dir / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    print(f"  reset:   {manifest.relative_to(ROOT)}")

    # --- parse manifest → delete ---
    parse_manifest = raw_dir / "parse_manifest.json"
    if parse_manifest.exists():
        parse_manifest.unlink()
        print(f"  deleted: {parse_manifest.relative_to(ROOT)}")

    # --- move files from typed folders back to _inbox/ ---
    inbox_dir.mkdir(parents=True, exist_ok=True)
    for folder_name in typed_folders:
        folder = raw_dir / folder_name
        if not folder.exists():
            continue
        moved = 0
        for f in folder.iterdir():
            if not f.is_file():
                continue
            dest = inbox_dir / f.name
            if dest.exists():
                # avoid collision — keep both with suffix
                dest = inbox_dir / f"{f.stem}_from_{folder_name}{f.suffix}"
            f.rename(dest)
            moved += 1
        if moved:
            print(f"  restored: {moved} file(s) from raw/{folder_name}/ → raw/_inbox/")
        folder.rmdir()

    # --- compiled wiki ---
    # Without this the events are cleared but the articles they produced are
    # not, so the next run compiles against half-cleared state.
    vault_project = ROOT / "kb" / "projects" / project_id
    if vault_project.exists():
        shutil.rmtree(vault_project)
        print(f"  deleted: {vault_project.relative_to(ROOT)}/")

    compile_manifest = raw_dir / "compile_manifest.json"
    if compile_manifest.exists():
        compile_manifest.unlink()
        print(f"  deleted: {compile_manifest.relative_to(ROOT)}")

    print(f"\nDone. '{project_id}' is ready for a fresh run.")
    print(f"  docker compose run --rm pipeline python -m pipeline run --project {project_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset a demo project to pre-run state.")
    parser.add_argument("--project", default="demo--sample-2026",
                        help="Project ID to reset (default: demo--sample-2026)")
    parser.add_argument("--keep-cache", action="store_true",
                        help="Keep LLM cache — re-run will replay cached responses (free, fast)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip confirmation prompt")
    args = parser.parse_args()
    reset(args.project, args.keep_cache, args.yes)


if __name__ == "__main__":
    main()
