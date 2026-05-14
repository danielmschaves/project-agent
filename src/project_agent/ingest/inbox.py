from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_EXT_TO_TYPE: dict[str, str] = {
    ".eml": "email",
    ".msg": "email",
    ".md": "doc",
    ".txt": "doc",
    ".pdf": "doc",
    ".docx": "doc",
    ".json": "backlog",
    ".csv": "backlog",
}

_TYPE_TO_FOLDER: dict[str, str] = {
    "email": "emails",
    "doc": "docs",
    "backlog": "backlog",
    "meeting": "meetings",
}


class IngestResult(BaseModel):
    filename: str
    source_type: str
    size_bytes: int
    source_hash: str
    source_ref: str  # path relative to project_dir, forward-slash separated
    is_new: bool


def _classify(filename: str) -> str:
    return _EXT_TO_TYPE.get(Path(filename).suffix.lower(), "doc")


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load_manifest(manifest_path: Path) -> dict[str, str]:
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return {}


def _save_manifest(manifest_path: Path, manifest: dict[str, str]) -> None:
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def scan_inbox(project_dir: Path) -> list[IngestResult]:
    """
    Scan sources/_inbox/, classify each file, move it to its typed folder,
    and return one IngestResult per newly seen file.

    Files already recorded in manifest.json (keyed by source_hash) are skipped
    so re-running produces zero new results — the source-level idempotence gate
    (PRD §6.2).
    """
    inbox_dir = project_dir / "sources" / "_inbox"
    manifest_path = project_dir / "sources" / "manifest.json"

    manifest = _load_manifest(manifest_path)
    results: list[IngestResult] = []

    if not inbox_dir.exists():
        logger.debug("No _inbox directory at %s", inbox_dir)
        return results

    for file_path in sorted(inbox_dir.iterdir()):
        if not file_path.is_file():
            continue

        source_hash = _sha256(file_path)

        if source_hash in manifest:
            logger.debug("Skip %s: hash already in manifest", file_path.name)
            continue

        source_type = _classify(file_path.name)
        dest_dir = project_dir / "sources" / _TYPE_TO_FOLDER.get(source_type, "docs")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / file_path.name

        # Avoid overwriting an existing file with a different hash
        if dest_path.exists():
            dest_path = dest_dir / f"{file_path.stem}_{source_hash[7:15]}{file_path.suffix}"

        shutil.move(str(file_path), str(dest_path))

        size_bytes = dest_path.stat().st_size
        source_ref = dest_path.relative_to(project_dir).as_posix()

        manifest[source_hash] = source_ref
        _save_manifest(manifest_path, manifest)

        logger.info("Ingested %s → %s (%d bytes)", file_path.name, source_ref, size_bytes)

        results.append(
            IngestResult(
                filename=file_path.name,
                source_type=source_type,
                size_bytes=size_bytes,
                source_hash=source_hash,
                source_ref=source_ref,
                is_new=True,
            )
        )

    return results
