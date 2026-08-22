"""Tests for project_agent.ingest.mcp — Gmail, Drive, Calendar adapters."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from project_agent.ingest.mcp import (
    _event_to_markdown,
    fetch_calendar,
    fetch_drive,
    fetch_gmail,
)
from project_agent.ingest.inbox import scan_inbox


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inbox(tmp_path: Path) -> Path:
    inbox = tmp_path / "raw" / "_inbox"
    inbox.mkdir(parents=True)
    (tmp_path / "raw" / "manifest.json").write_text("{}", encoding="utf-8")
    for folder in ("emails", "docs", "backlog", "meetings"):
        (tmp_path / "raw" / folder).mkdir()
    return inbox


RAW_EMAIL = (
    b"From: alice@example.com\r\n"
    b"To: bob@example.com\r\n"
    b"Date: Mon, 12 May 2026 10:00:00 +0000\r\n"
    b"Subject: Status update\r\n"
    b"\r\n"
    b"Hi Bob, please action the deployment by Friday.\r\n"
)
import base64 as _b64
RAW_EMAIL_B64 = _b64.urlsafe_b64encode(RAW_EMAIL).decode()


# ---------------------------------------------------------------------------
# Unit tests — fetch_gmail
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fetch_gmail_writes_eml_file(tmp_path: Path) -> None:
    inbox = _make_inbox(tmp_path)
    fake_creds = MagicMock()

    mock_service = MagicMock()
    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "abc123def456"}]
    }
    mock_service.users().messages().get().execute.return_value = {"raw": RAW_EMAIL_B64}

    with patch("googleapiclient.discovery.build", return_value=mock_service):
        written = fetch_gmail("test--project", inbox, fake_creds)

    assert len(written) == 1
    assert written[0].suffix == ".eml"
    assert written[0].read_bytes() == RAW_EMAIL


@pytest.mark.unit
def test_fetch_gmail_skips_existing_file(tmp_path: Path) -> None:
    inbox = _make_inbox(tmp_path)
    fake_creds = MagicMock()

    # Pre-create the file at the name the adapter would generate
    existing = inbox / "2026-05-12-gmail-abc123def456.eml"
    existing.write_bytes(b"existing")
    # Adapter parses RFC 2822 date "Mon, 12 May 2026 10:00:00 +0000" → 2026-05-12

    mock_service = MagicMock()
    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "abc123def456"}]
    }
    mock_service.users().messages().get().execute.return_value = {"raw": RAW_EMAIL_B64}

    with patch("googleapiclient.discovery.build", return_value=mock_service):
        written = fetch_gmail("test--project", inbox, fake_creds)

    assert written == []
    # Original file untouched
    assert existing.read_bytes() == b"existing"


@pytest.mark.unit
def test_fetch_gmail_handles_refresh_error_gracefully(tmp_path: Path) -> None:
    inbox = _make_inbox(tmp_path)
    fake_creds = MagicMock()

    from google.auth.exceptions import RefreshError

    with patch("googleapiclient.discovery.build", side_effect=RefreshError("token expired")):
        written = fetch_gmail("test--project", inbox, fake_creds)

    assert written == []


@pytest.mark.unit
def test_fetch_gmail_handles_api_error_gracefully(tmp_path: Path) -> None:
    inbox = _make_inbox(tmp_path)
    fake_creds = MagicMock()

    with patch("googleapiclient.discovery.build", side_effect=Exception("network error")):
        written = fetch_gmail("test--project", inbox, fake_creds)

    assert written == []


# ---------------------------------------------------------------------------
# Unit tests — fetch_drive
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fetch_drive_exports_google_doc(tmp_path: Path) -> None:
    inbox = _make_inbox(tmp_path)
    fake_creds = MagicMock()

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {
        "files": [{
            "id": "doc123",
            "name": "Project Spec",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-05-10T09:00:00Z",
        }]
    }
    mock_service.files().export().execute.return_value = b"# Project Spec\n\nAction: Deploy by Friday"

    with patch("googleapiclient.discovery.build", return_value=mock_service):
        written = fetch_drive("test--project", inbox, fake_creds)

    assert len(written) == 1
    assert written[0].suffix == ".md"
    assert b"Deploy by Friday" in written[0].read_bytes()


@pytest.mark.unit
def test_fetch_drive_downloads_markdown_file(tmp_path: Path) -> None:
    inbox = _make_inbox(tmp_path)
    fake_creds = MagicMock()

    content = b"# Meeting Notes\n\nBlocker: awaiting sign-off from legal"
    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {
        "files": [{
            "id": "md123",
            "name": "notes.md",
            "mimeType": "text/markdown",
            "modifiedTime": "2026-05-11T14:00:00Z",
        }]
    }
    mock_service.files().get_media().execute.return_value = content

    with patch("googleapiclient.discovery.build", return_value=mock_service):
        written = fetch_drive("test--project", inbox, fake_creds)

    assert len(written) == 1
    assert b"sign-off from legal" in written[0].read_bytes()


@pytest.mark.unit
def test_fetch_drive_skips_unsupported_mime(tmp_path: Path) -> None:
    inbox = _make_inbox(tmp_path)
    fake_creds = MagicMock()

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {
        "files": [{
            "id": "img123",
            "name": "logo.png",
            "mimeType": "image/png",
            "modifiedTime": "2026-05-10T09:00:00Z",
        }]
    }

    with patch("googleapiclient.discovery.build", return_value=mock_service):
        written = fetch_drive("test--project", inbox, fake_creds)

    assert written == []


@pytest.mark.unit
def test_fetch_drive_handles_refresh_error_gracefully(tmp_path: Path) -> None:
    inbox = _make_inbox(tmp_path)
    fake_creds = MagicMock()
    from google.auth.exceptions import RefreshError

    with patch("googleapiclient.discovery.build", side_effect=RefreshError("expired")):
        written = fetch_drive("test--project", inbox, fake_creds)

    assert written == []


# ---------------------------------------------------------------------------
# Unit tests — fetch_calendar
# ---------------------------------------------------------------------------

FAKE_EVENT = {
    "id": "evt001abc123",
    "summary": "Weekly Standup",
    "start": {"dateTime": "2026-05-12T10:00:00Z"},
    "end": {"dateTime": "2026-05-12T10:30:00Z"},
    "location": "Google Meet",
    "description": "Action: update the risk register before EOD.\nBlocker: waiting on partner API.",
    "attendees": [
        {"displayName": "Alice", "email": "alice@example.com"},
        {"displayName": "Bob", "email": "bob@example.com"},
    ],
}


@pytest.mark.unit
def test_fetch_calendar_writes_markdown_file(tmp_path: Path) -> None:
    inbox = _make_inbox(tmp_path)
    fake_creds = MagicMock()

    mock_service = MagicMock()
    mock_service.events().list().execute.return_value = {"items": [FAKE_EVENT]}

    with patch("googleapiclient.discovery.build", return_value=mock_service):
        written = fetch_calendar("test--project", inbox, fake_creds)

    assert len(written) == 1
    content = written[0].read_text(encoding="utf-8")
    assert "Weekly Standup" in content
    assert "risk register" in content
    assert "partner API" in content


@pytest.mark.unit
def test_fetch_calendar_skips_existing_file(tmp_path: Path) -> None:
    inbox = _make_inbox(tmp_path)
    fake_creds = MagicMock()

    # Pre-create the file that would be written
    existing = inbox / "2026-05-12-cal-Weekly_Standup-evt001abc123.md"
    existing.write_text("old content", encoding="utf-8")

    mock_service = MagicMock()
    mock_service.events().list().execute.return_value = {"items": [FAKE_EVENT]}

    with patch("googleapiclient.discovery.build", return_value=mock_service):
        written = fetch_calendar("test--project", inbox, fake_creds)

    assert written == []
    assert existing.read_text() == "old content"


@pytest.mark.unit
def test_fetch_calendar_handles_refresh_error_gracefully(tmp_path: Path) -> None:
    inbox = _make_inbox(tmp_path)
    fake_creds = MagicMock()
    from google.auth.exceptions import RefreshError

    with patch("googleapiclient.discovery.build", side_effect=RefreshError("expired")):
        written = fetch_calendar("test--project", inbox, fake_creds)

    assert written == []


# ---------------------------------------------------------------------------
# Unit tests — _event_to_markdown
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_event_to_markdown_includes_all_fields() -> None:
    md = _event_to_markdown(FAKE_EVENT)
    assert "# Weekly Standup" in md
    assert "2026-05-12" in md
    assert "Google Meet" in md
    assert "Alice" in md
    assert "risk register" in md


@pytest.mark.unit
def test_event_to_markdown_handles_missing_fields() -> None:
    minimal = {"id": "x", "summary": "Minimal Meeting", "start": {"date": "2026-05-10"}}
    md = _event_to_markdown(minimal)
    assert "# Minimal Meeting" in md
    assert "2026-05-10" in md


# ---------------------------------------------------------------------------
# Idempotence — MCP fetch + scan_inbox second run produces zero new results
# ---------------------------------------------------------------------------

@pytest.mark.idempotence
def test_mcp_ingest_idempotent_via_manifest(tmp_path: Path) -> None:
    """Fetch once → scan_inbox processes file → manifest updated.
    Fetch same content again → scan_inbox skips (hash in manifest) → deletes from inbox.
    Net result: zero new IngestResult on second round."""
    inbox_dir = _make_inbox(tmp_path)
    fake_creds = MagicMock()

    mock_service = MagicMock()
    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "abc123def456"}]
    }
    mock_service.users().messages().get().execute.return_value = {"raw": RAW_EMAIL_B64}

    with patch("googleapiclient.discovery.build", return_value=mock_service):
        fetch_gmail("test--project", inbox_dir, fake_creds)

    # First scan_inbox call — processes the file, adds hash to manifest
    first = scan_inbox(tmp_path)
    assert len(first) == 1
    assert first[0].is_new

    # Second round: adapter would write the file again (it's gone from inbox)
    with patch("googleapiclient.discovery.build", return_value=mock_service):
        fetch_gmail("test--project", inbox_dir, fake_creds)

    # File is back in _inbox/ but scan_inbox finds hash in manifest → deletes it → zero new
    second = scan_inbox(tmp_path)
    assert second == []
    # _inbox is clean — file was deleted by scan_inbox
    assert not list(inbox_dir.iterdir())


@pytest.mark.idempotence
def test_calendar_ingest_idempotent(tmp_path: Path) -> None:
    inbox_dir = _make_inbox(tmp_path)
    fake_creds = MagicMock()

    mock_service = MagicMock()
    mock_service.events().list().execute.return_value = {"items": [FAKE_EVENT]}

    with patch("googleapiclient.discovery.build", return_value=mock_service):
        fetch_calendar("test--project", inbox_dir, fake_creds)

    first = scan_inbox(tmp_path)
    assert len(first) == 1

    # Second fetch round — adapter writes same content (file gone from inbox)
    with patch("googleapiclient.discovery.build", return_value=mock_service):
        fetch_calendar("test--project", inbox_dir, fake_creds)

    second = scan_inbox(tmp_path)
    assert second == []
    assert not list(inbox_dir.iterdir())
