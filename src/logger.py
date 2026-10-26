"""Writes every input, output, tool call, token count and timing as one JSON line to runs/<timestamp>/log.jsonl."""

import dataclasses
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from src import config


def _to_jsonable(value: object) -> object:
    """Turn our objects (Pydantic models, dataclasses, paths) into plain JSON values."""
    if isinstance(value, BaseModel):
        return value.model_dump()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return str(value)  # anything else (Path, SDK message objects): readable, never a crash


def fingerprint(text: str) -> dict:
    """Length and short hash of a text, logged INSTEAD of page text (ground rule: pages can name people).

    The full text stays only in cache/; the hash proves later which version the agent saw.
    """
    return {"length": len(text), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]}


def redact_tool_messages(messages: list[dict]) -> list[dict]:
    """Copy of a conversation with every tool result's text replaced by its fingerprint."""
    redacted = []
    for message in messages:
        if message.get("role") == "tool":
            message = {**message, "content": fingerprint(message.get("content") or "")}
        redacted.append(message)
    return redacted


class RunLog:
    """One run's folder and its log.jsonl. Each event is one line, so a crash loses nothing written."""

    def __init__(self, label: str, run_dir: Path | None = None) -> None:
        """Create runs/<timestamp>_<label>/, or reuse run_dir if given (e.g. for merging parts)."""
        if run_dir is None:
            # No colons: Windows doesn't allow them in folder names
            stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            run_dir = config.RUNS_DIR / f"{stamp}_{label}"
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "log.jsonl"

    def event(self, role: str, company: str | None = None, input: object = None, output: object = None,
              usage: object = None, duration: float | None = None, **extra: object) -> None:
        """Append one event. role says who acted: 'single_agent', 'tool:web_search', 'run', ..."""
        record = {"time": datetime.now().isoformat(timespec="seconds"), "role": role, "company": company,
                  "input": input, "output": output, "usage": usage,
                  "duration_s": round(duration, 2) if duration is not None else None, **extra}
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, default=_to_jsonable, ensure_ascii=False) + "\n")


class Timer:
    """Measures how long a block took: with Timer() as t: ...; then t.seconds."""

    def __enter__(self) -> "Timer":
        self.start = time.monotonic()
        self.seconds = 0.0
        return self

    def __exit__(self, *exc: object) -> None:
        self.seconds = time.monotonic() - self.start

    def elapsed(self) -> float:
        """Seconds so far, readable while the block is still running."""
        return time.monotonic() - self.start
