from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Protocol, cast

import frontmatter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Pricing in USD per token (claude-sonnet-4-6, as of 2026-05)
_COST_PER_INPUT_TOKEN: float = 3.0 / 1_000_000
_COST_PER_OUTPUT_TOKEN: float = 15.0 / 1_000_000


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

class Prompt(BaseModel):
    """A versioned prompt loaded from prompts/<name>.md."""

    name: str
    text: str
    version: int
    max_tokens: int = 4096


def load_prompt(name: str, prompts_dir: Path) -> Prompt:
    """Load prompts/<name>.md and its front-matter."""
    path = prompts_dir / f"{name}.md"
    post = frontmatter.load(str(path))
    return Prompt(
        name=name,
        text=str(post.content),
        version=int(post.metadata["version"]),
        max_tokens=int(post.metadata.get("max_tokens", 4096)),
    )


# ---------------------------------------------------------------------------
# LLM response cache + extraction
# ---------------------------------------------------------------------------

def _cache_key(prompt_name: str, prompt_version: int, source_hash: str, model: str) -> str:
    """Cache identity for one LLM call.

    The prompt *name* is part of the key on purpose: without it, two different
    prompts sitting at the same version, over the same source and model, hash
    to the same key and replay each other's responses.
    """
    raw = f"{prompt_name}:{prompt_version}:{source_hash}:{model}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _strip_code_fence(text: str) -> str:
    """Drop the ``` fences some models wrap around JSON output."""
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
    return "\n".join(lines[1:end]).strip()


def _default_client() -> _Client:
    """The real Anthropic client, imported lazily so tests never need the package."""
    import anthropic

    return cast(_Client, anthropic.Anthropic())


def _estimate_cost(message: Any) -> float:
    """Estimate USD cost from the usage block, 0.0 when the model reports none."""
    usage = getattr(message, "usage", None)
    if usage is None:
        return 0.0
    return (
        getattr(usage, "input_tokens", 0) * _COST_PER_INPUT_TOKEN
        + getattr(usage, "output_tokens", 0) * _COST_PER_OUTPUT_TOKEN
    )


def is_cached(
    prompt_name: str,
    source_hash: str,
    model: str,
    cache_dir: Path,
    prompts_dir: Path,
) -> bool:
    """Whether this exact call would replay from cache instead of hitting the API.

    Callers working under a spend cap need to know this *before* consulting the
    budget: a cached response costs nothing, so an exhausted budget must never
    stop it being replayed. Otherwise a run that hits its cap would regress
    already-generated content back to a cheaper fallback.
    """
    prompt = load_prompt(prompt_name, prompts_dir)
    key = _cache_key(prompt.name, prompt.version, source_hash, model)
    return (cache_dir / f"{key}.json").exists()


def extract_with_cost(
    prompt_name: str,
    source_text: str,
    source_hash: str,
    model: str,
    cache_dir: Path,
    prompts_dir: Path,
    client: _Client | None = None,
) -> tuple[dict[str, Any], float]:
    """Extract structured JSON from source_text using the named prompt.

    Cache key: sha256(prompt_name:prompt_version:source_hash:model).  Cache
    hits replay the stored JSON without calling the API — this is the
    idempotence gate for the parse and compile stages (PRD §6.2, Rule 3 in
    CLAUDE.md).

    Returns (result, cost_usd).  cost_usd is 0.0 on a cache hit — idempotent
    re-runs are free.  On a live call, cost is estimated from the token usage
    reported by the response object.
    """
    prompt = load_prompt(prompt_name, prompts_dir)
    key = _cache_key(prompt.name, prompt.version, source_hash, model)
    cache_file = cache_dir / f"{key}.json"

    if cache_file.exists():
        logger.debug("LLM cache hit: %s", key[:12])
        return dict(json.loads(cache_file.read_text(encoding="utf-8"))), 0.0

    active_client = client if client is not None else _default_client()

    logger.info(
        "LLM cache miss — calling API: %s@%d model=%s", prompt.name, prompt.version, model
    )
    message = active_client.messages.create(
        model=model,
        max_tokens=prompt.max_tokens,
        messages=[{"role": "user", "content": f"{prompt.text}\n\n---\n\n{source_text}"}],
    )
    response_text = _strip_code_fence(message.content[0].text.strip())

    if not response_text:
        raise ValueError(
            f"LLM returned empty response for prompt={prompt.name}@{prompt.version} model={model}"
        )

    result: dict[str, Any] = json.loads(response_text)
    cost_usd = _estimate_cost(message)

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.debug("LLM response cached: %s (cost=%.6f USD)", key[:12], cost_usd)

    return result, cost_usd


def extract(
    prompt_name: str,
    source_text: str,
    source_hash: str,
    model: str,
    cache_dir: Path,
    prompts_dir: Path,
    client: _Client | None = None,
) -> dict[str, Any]:
    """extract_with_cost() without the cost — see there for cache semantics."""
    result, _ = extract_with_cost(
        prompt_name, source_text, source_hash, model, cache_dir, prompts_dir, client
    )
    return result
