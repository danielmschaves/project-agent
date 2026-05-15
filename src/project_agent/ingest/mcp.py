"""MCP ingest adapters — Gmail, Drive, Calendar (Phase 0.5, PRD §8.1).

Each adapter fetches content from a Google service and writes raw files to the
project's _inbox/ directory.  Downstream `inbox.scan_inbox` handles
classification, manifest dedup, and event emission — nothing here emits events.

Idempotence: the same fetched content produces the same bytes → same
source_hash → skipped by manifest.json on the next `scan_inbox` call.
Files that land in _inbox/ and are already in the manifest are removed by
`scan_inbox` so _inbox/ does not accumulate stale duplicates.

Authentication: pass the path to a token.json (OAuth2 authorized-user format)
via GOOGLE_TOKEN_PATH, or call build_credentials() directly and inject creds.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.utils import parsedate_to_datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def build_credentials(
    token_path: Path,
    scopes: list[str] | None = None,
) -> object:
    """Load OAuth2 credentials from a token.json, refreshing if expired.

    Returns a google.oauth2.credentials.Credentials instance.
    The token file is updated in-place when a refresh occurs.
    """
    from google.auth.transport.requests import Request  # type: ignore[import-untyped]
    from google.oauth2.credentials import Credentials  # type: ignore[import-untyped]

    if scopes is None:
        scopes = SCOPES

    data = json.loads(token_path.read_text(encoding="utf-8"))
    creds: Credentials = Credentials.from_authorized_user_info(data, scopes)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
        logger.debug("Refreshed OAuth token; updated %s", token_path)

    return creds


def fetch_gmail(
    project_id: str,
    inbox_dir: Path,
    creds: object,
    since: datetime | None = None,
) -> list[Path]:
    """Fetch recent Gmail messages and write them as .eml files to inbox_dir.

    Returns the list of newly written file paths.
    OAuth refresh failures are caught and logged; fetch is skipped gracefully.
    """
    from google.auth.exceptions import RefreshError  # type: ignore[import-untyped]
    from googleapiclient.discovery import build  # type: ignore[import-untyped]

    if since is None:
        since = datetime.now(tz=timezone.utc) - timedelta(days=7)

    inbox_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    try:
        service = build("gmail", "v1", credentials=creds)
        after_str = since.strftime("%Y/%m/%d")
        response = (
            service.users()
            .messages()
            .list(userId="me", q=f"after:{after_str}", maxResults=50)
            .execute()
        )
        for msg_ref in response.get("messages", []):
            try:
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=msg_ref["id"], format="raw")
                    .execute()
                )
                raw_bytes = base64.urlsafe_b64decode(msg["raw"] + "==")
                parsed = message_from_bytes(raw_bytes)
                try:
                    date_prefix = parsedate_to_datetime(parsed.get("Date") or "").strftime("%Y-%m-%d")
                except Exception:
                    date_prefix = "unknown"
                msg_id_short = msg_ref["id"][:12]
                filename = f"{date_prefix}-gmail-{msg_id_short}.eml"

                dest = inbox_dir / filename
                if not dest.exists():
                    dest.write_bytes(raw_bytes)
                    written.append(dest)
                    logger.debug("Gmail: wrote %s (%d bytes)", filename, len(raw_bytes))
            except Exception:
                logger.exception("Gmail: failed to process message id=%s; skipping", msg_ref.get("id"))

    except RefreshError:
        logger.warning("Gmail: OAuth token refresh failed — skipping Gmail ingest")
    except Exception:
        logger.exception("Gmail: fetch failed — skipping Gmail ingest for project %s", project_id)

    logger.info("Gmail: %d new message(s) written to inbox", len(written))
    return written


def fetch_drive(
    project_id: str,
    inbox_dir: Path,
    creds: object,
    since: datetime | None = None,
) -> list[Path]:
    """Fetch recently modified Drive files and write them to inbox_dir.

    Google Docs are exported as plain text (.md); native text/markdown and
    .docx files are downloaded as-is.  Other MIME types are skipped.
    """
    from google.auth.exceptions import RefreshError  # type: ignore[import-untyped]
    from googleapiclient.discovery import build  # type: ignore[import-untyped]

    if since is None:
        since = datetime.now(tz=timezone.utc) - timedelta(days=7)

    inbox_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        service = build("drive", "v3", credentials=creds)
        response = (
            service.files()
            .list(
                q=f"modifiedTime > '{since_str}' and trashed = false",
                fields="files(id, name, mimeType, modifiedTime)",
                pageSize=50,
            )
            .execute()
        )
        for f in response.get("files", []):
            try:
                mime = f.get("mimeType", "")
                name = f.get("name", f["id"])
                safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
                date_prefix = (f.get("modifiedTime") or "")[:10]

                if mime == "application/vnd.google-apps.document":
                    content = service.files().export(fileId=f["id"], mimeType="text/plain").execute()
                    filename = f"{date_prefix}-drive-{safe_name}.md"
                    dest = inbox_dir / filename
                    if not dest.exists():
                        dest.write_bytes(content if isinstance(content, bytes) else content.encode())
                        written.append(dest)
                        logger.debug("Drive: exported Google Doc → %s", filename)

                elif mime in ("text/plain", "text/markdown"):
                    content = service.files().get_media(fileId=f["id"]).execute()
                    filename = f"{date_prefix}-drive-{safe_name}.md"
                    dest = inbox_dir / filename
                    if not dest.exists():
                        dest.write_bytes(content if isinstance(content, bytes) else content.encode())
                        written.append(dest)
                        logger.debug("Drive: downloaded text file → %s", filename)

                elif mime == (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ):
                    content = service.files().get_media(fileId=f["id"]).execute()
                    filename = f"{date_prefix}-drive-{safe_name}.docx"
                    dest = inbox_dir / filename
                    if not dest.exists():
                        dest.write_bytes(content if isinstance(content, bytes) else content.encode())
                        written.append(dest)
                        logger.debug("Drive: downloaded docx → %s", filename)

                else:
                    logger.debug("Drive: skipping unsupported mime type %s for '%s'", mime, name)
            except Exception:
                logger.exception("Drive: failed to process file id=%s; skipping", f.get("id"))

    except RefreshError:
        logger.warning("Drive: OAuth token refresh failed — skipping Drive ingest")
    except Exception:
        logger.exception("Drive: fetch failed — skipping Drive ingest for project %s", project_id)

    logger.info("Drive: %d new file(s) written to inbox", len(written))
    return written


def fetch_calendar(
    project_id: str,
    inbox_dir: Path,
    creds: object,
    since: datetime | None = None,
) -> list[Path]:
    """Fetch recent Calendar events and write them as formatted .md files to inbox_dir.

    Each event becomes a markdown meeting note so the LLM parse stage can
    extract actions and blockers from meeting content.
    """
    from google.auth.exceptions import RefreshError  # type: ignore[import-untyped]
    from googleapiclient.discovery import build  # type: ignore[import-untyped]

    if since is None:
        since = datetime.now(tz=timezone.utc) - timedelta(days=7)

    inbox_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        service = build("calendar", "v3", credentials=creds)
        response = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=since_str,
                timeMax=now_str,
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            )
            .execute()
        )
        for evt in response.get("items", []):
            try:
                evt_id = evt.get("id", "unknown")[:12]
                start = evt.get("start", {})
                date_str = start.get("dateTime", start.get("date", ""))[:10]
                summary = evt.get("summary", "meeting")
                safe_summary = "".join(
                    c if c.isalnum() or c in "-_" else "_" for c in summary
                )[:40]
                filename = f"{date_str}-cal-{safe_summary}-{evt_id}.md"

                dest = inbox_dir / filename
                if not dest.exists():
                    md_content = _event_to_markdown(evt)
                    dest.write_text(md_content, encoding="utf-8")
                    written.append(dest)
                    logger.debug("Calendar: wrote meeting note → %s", filename)
            except Exception:
                logger.exception("Calendar: failed to process event id=%s; skipping", evt.get("id"))

    except RefreshError:
        logger.warning("Calendar: OAuth token refresh failed — skipping Calendar ingest")
    except Exception:
        logger.exception(
            "Calendar: fetch failed — skipping Calendar ingest for project %s", project_id
        )

    logger.info("Calendar: %d new event(s) written to inbox", len(written))
    return written


def _event_to_markdown(evt: dict[str, object]) -> str:
    """Format a Calendar event dict as a markdown meeting note."""
    summary = evt.get("summary", "Meeting")
    start = evt.get("start", {})
    assert isinstance(start, dict)
    end = evt.get("end", {})
    assert isinstance(end, dict)
    start_time = start.get("dateTime", start.get("date", ""))
    end_time = end.get("dateTime", end.get("date", ""))
    location = evt.get("location", "")
    description = evt.get("description", "")
    attendees_raw = evt.get("attendees", [])
    assert isinstance(attendees_raw, list)
    attendee_names = ", ".join(
        str(a.get("displayName") or a.get("email", "")) for a in attendees_raw
    )

    lines = [f"# {summary}", ""]
    if start_time:
        lines += [f"**Date:** {start_time}", ""]
    if end_time and end_time != start_time:
        lines += [f"**End:** {end_time}", ""]
    if location:
        lines += [f"**Location:** {location}", ""]
    if attendee_names:
        lines += [f"**Attendees:** {attendee_names}", ""]
    if description:
        lines += ["## Description", "", str(description), ""]

    return "\n".join(lines)
