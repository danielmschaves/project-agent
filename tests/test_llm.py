"""Tests for project_agent.llm (load_prompt, extract, cache)."""
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from project_agent import llm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_prompt(prompts_dir: Path, name: str = "extract-context", version: int = 1, body: str = "Extract facts.") -> None:
    (prompts_dir / f"{name}.md").write_text(
        f"---\nversion: {version}\npurpose: test\n---\n{body}", encoding="utf-8"
    )


def _make_fake_client(response: dict[str, Any]) -> Any:
    """Fake Anthropic client returning a fixed JSON response."""
    response_text = json.dumps(response)

    class _Content:
        text = response_text

    class _Message:
        content = [_Content()]

    class _Messages:
        @staticmethod
        def create(**kwargs: Any) -> _Message:
            return _Message()

    class _Client:
        messages = _Messages()

    return _Client()


def _raising_client() -> Any:
    """Fake client that raises RuntimeError if the API is called."""
    class _Messages:
        @staticmethod
        def create(**kwargs: Any) -> Any:
            raise RuntimeError("Anthropic API must not be called on cache hit")

    class _Client:
        messages = _Messages()

    return _Client()


# ---------------------------------------------------------------------------
# load_prompt
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_load_prompt_returns_text_and_version(tmp_path: Path) -> None:
    _write_prompt(tmp_path, version=3, body="Do something useful.")
    text, version = llm.load_prompt("extract-context", tmp_path)
    assert version == 3
    assert "Do something useful" in text


@pytest.mark.unit
def test_load_prompt_version_is_int(tmp_path: Path) -> None:
    _write_prompt(tmp_path, version=2)
    _, version = llm.load_prompt("extract-context", tmp_path)
    assert isinstance(version, int)


# ---------------------------------------------------------------------------
# Cache key stability
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_cache_key_is_deterministic(tmp_path: Path) -> None:
    _write_prompt(tmp_path, version=1)
    cache_dir = tmp_path / "cache"
    fake = _make_fake_client({"actions": [], "blockers": []})

    r1 = llm.extract("extract-context", "hello", "sha256:abc", "model-x", cache_dir, tmp_path, fake)
    # Pre-compute expected cache file
    key = hashlib.sha256(b"1:sha256:abc:model-x").hexdigest()
    assert (cache_dir / f"{key}.json").exists()
    assert r1 == {"actions": [], "blockers": []}


@pytest.mark.unit
def test_cache_key_differs_on_different_model(tmp_path: Path) -> None:
    _write_prompt(tmp_path, version=1)
    key_a = hashlib.sha256(b"1:sha256:abc:model-a").hexdigest()
    key_b = hashlib.sha256(b"1:sha256:abc:model-b").hexdigest()
    assert key_a != key_b


@pytest.mark.unit
def test_cache_key_differs_on_different_prompt_version(tmp_path: Path) -> None:
    key_v1 = hashlib.sha256(b"1:sha256:abc:model-x").hexdigest()
    key_v2 = hashlib.sha256(b"2:sha256:abc:model-x").hexdigest()
    assert key_v1 != key_v2


# ---------------------------------------------------------------------------
# Cache miss → API call → result cached
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_extract_cache_miss_calls_api_and_writes_cache(tmp_path: Path) -> None:
    _write_prompt(tmp_path, version=1)
    cache_dir = tmp_path / "cache"
    response = {"actions": [{"description": "Do thing", "owner": None, "due": None, "status": "open"}], "blockers": []}
    fake = _make_fake_client(response)

    result = llm.extract("extract-context", "source text", "sha256:xyz", "model-x", cache_dir, tmp_path, fake)

    assert result == response
    key = hashlib.sha256(b"1:sha256:xyz:model-x").hexdigest()
    assert (cache_dir / f"{key}.json").exists()


@pytest.mark.integration
def test_extract_second_call_is_cache_hit(tmp_path: Path) -> None:
    _write_prompt(tmp_path, version=1)
    cache_dir = tmp_path / "cache"
    call_count = 0

    class _CountingMessages:
        @staticmethod
        def create(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1

            class _C:
                text = '{"actions": [], "blockers": []}'

            class _M:
                content = [_C()]

            return _M()

    class _CountingClient:
        messages = _CountingMessages()

    r1 = llm.extract("extract-context", "text", "sha256:abc", "model-x", cache_dir, tmp_path, _CountingClient())
    r2 = llm.extract("extract-context", "text", "sha256:abc", "model-x", cache_dir, tmp_path, _CountingClient())

    assert call_count == 1  # second call must be a cache hit
    assert r1 == r2


# ---------------------------------------------------------------------------
# Idempotence — raising client must never be invoked on cache hit
# ---------------------------------------------------------------------------

@pytest.mark.idempotence
def test_extract_cache_hit_never_calls_api(tmp_path: Path) -> None:
    _write_prompt(tmp_path, version=1)
    cache_dir = tmp_path / "cache"
    payload = {"actions": [], "blockers": []}

    # First call: populate cache with a real fake client
    llm.extract("extract-context", "text", "sha256:aaa", "model-x", cache_dir, tmp_path, _make_fake_client(payload))

    # Second call: raising client — must NOT raise
    result = llm.extract("extract-context", "text", "sha256:aaa", "model-x", cache_dir, tmp_path, _raising_client())
    assert result == payload


@pytest.mark.idempotence
def test_bumping_prompt_version_invalidates_cache(tmp_path: Path) -> None:
    """After bumping the prompt version, the cache miss path must be taken."""
    call_count = 0

    class _CountingMessages:
        @staticmethod
        def create(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1

            class _C:
                text = '{"actions": [], "blockers": []}'

            class _M:
                content = [_C()]

            return _M()

    class _CountingClient:
        messages = _CountingMessages()

    cache_dir = tmp_path / "cache"

    _write_prompt(tmp_path, version=1)
    llm.extract("extract-context", "text", "sha256:aaa", "model-x", cache_dir, tmp_path, _CountingClient())
    assert call_count == 1

    # Bump version — different cache key → cache miss
    _write_prompt(tmp_path, version=2)
    llm.extract("extract-context", "text", "sha256:aaa", "model-x", cache_dir, tmp_path, _CountingClient())
    assert call_count == 2
