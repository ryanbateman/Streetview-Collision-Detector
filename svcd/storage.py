"""JSONL and CSV persistence for the dataclasses in svcd.models.

Datetime fields are serialised as ISO 8601 UTC strings and parsed back using the
dataclass type hints, so callers never hand-write (de)serialisation.
"""
import csv
import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, TypeVar, get_args, get_type_hints

T = TypeVar("T")


def _isDatetimeHint(hint: Any) -> bool:
    return hint is datetime or datetime in get_args(hint)


def _toUtcIso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def toRecord(obj: Any) -> dict[str, Any]:
    """Dataclass instance to a JSON-safe dict."""
    record: dict[str, Any] = {}
    for field in dataclasses.fields(obj):
        value = getattr(obj, field.name)
        if isinstance(value, datetime):
            value = _toUtcIso(value)
        record[field.name] = value
    return record


def fromRecord(cls: type[T], record: dict[str, Any]) -> T:
    """Dict (as produced by toRecord) back to a dataclass instance."""
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        value = record.get(field.name)
        if value is not None and _isDatetimeHint(hints[field.name]):
            value = datetime.fromisoformat(value)
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
        kwargs[field.name] = value
    return cls(**kwargs)


def writeJsonl(path: Path | str, rows: Iterable[Any], append: bool = False) -> int:
    """Write dataclass rows one JSON object per line. Returns the row count written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a" if append else "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(toRecord(row), ensure_ascii=False))
            handle.write("\n")
            count += 1
    return count


def appendJsonl(path: Path | str, rows: Iterable[Any]) -> int:
    return writeJsonl(path, rows, append=True)


def iterJsonl(path: Path | str, cls: type[T]) -> Iterator[T]:
    """Lazily yield dataclass rows from a JSONL file. A missing file yields nothing."""
    path = Path(path)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield fromRecord(cls, json.loads(line))


def readJsonl(path: Path | str, cls: type[T]) -> list[T]:
    return list(iterJsonl(path, cls))


def writeCsv(path: Path | str, rows: Iterable[Any], cls: type | None = None) -> int:
    """Write dataclass rows as CSV with a header. cls is needed only when rows may be empty."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    if rows:
        fieldnames = [field.name for field in dataclasses.fields(rows[0])]
    elif cls is not None:
        fieldnames = [field.name for field in dataclasses.fields(cls)]
    else:
        raise ValueError("writeCsv needs cls when rows is empty")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(toRecord(row))
    return len(rows)
