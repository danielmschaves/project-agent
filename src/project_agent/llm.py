from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Protocol

import frontmatter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Minimal Protocol so tests can inject a fake client without importing anthropic
# ---------------------------------------------------------------------------

class _ContentBlock(Protocol):
    @property
    def text(self) -> str: ...


class _Message(Protocol):
    @property
    def content(self) -> list[_ContentBlock]: ...


class _Messages(Protocol):
    def create(self, **kwargs: Any) -> _Message: ...


class _Client(Protocol):
    @property
    def messages(self) -> _Messages: ...


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

def load_prompt(name: str, prompts_dir: Path) -> tuple[str, int]:
    """Return (prompt_text, version) from prompts/<name>.md front-matter."""
    path = prompts_dir / f"{name}.md"
    post = frontmatter.load(str(path))
    version = int(post.metadata["version"])
    return str(post.content), version


# ---------------------------------------------------------------------------
# LLM response cache + extraction
# ---------------------------------------------------------------------------

def _cache_key(prompt_version: int, source_hash: str, model: str) -> str:
    raw = f"{prompt_version}:{source_hash}:{model}"
    return hashlib.sha256(raw.encode()).hexdigest()


def extract(
    prompt_name: str,
    source_text: str,
    source_hash: str,
    model: str,
    cache_dir: Path,
    prompts_dir: Path,
    client: _Client | None = None,
) -> dict[str, Any]:
    """Extract structured facts from source_text using the named prompt.

    Cache key: sha256(prompt_version:source_hash:model).  Cache hits replay
    the stored JSON without calling the API — this is the idempotence gate
    for the parse stage (PRD §6.2, Rule 3 in CLAUDE.md).
    """
    prompt_text, prompt_version = load_prompt(prompt_name, prompts_dir)
    key = _cache_key(prompt_version, source_hash, model)
    cache_file = cache_dir / f"{key}.json"

    if cache_file.exists():
        logger.debug("LLM cache hit: %s", key[:12])
        return dict(json.loads(cache_file.read_text(encoding="utf-8")))

    if client is None:
        import anthropic  # deferred so tests never need the package installed
        client = anthropic.Anthropic()  # type: ignore[assignment]

    logger.info("LLM cache miss — calling API: %s@%d model=%s", prompt_name, prompt_version, model)
    message = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": f"{prompt_text}\n\n---\n\n{source_text}"}],
    )
    response_text = message.content[0].text.strip()

    # Strip markdown code fences some models wrap around JSON output
    if response_text.startswith("```"):
        lines = response_text.splitlines()
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        response_text = "\n".join(lines[1:end]).strip()

    if not response_text:
        raise ValueError(
            f"LLM returned empty response for prompt={prompt_name}@{prompt_version} model={model}"
        )

    result: dict[str, Any] = json.loads(response_text)

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.debug("LLM response cached: %s", key[:12])

    return result
