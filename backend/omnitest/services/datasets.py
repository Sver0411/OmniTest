"""Bounded CSV/JSON parsing with explicit typing and import provenance."""

import csv
import hashlib
import io
import json
import math
from typing import Literal

from pydantic import Field

from omnitest.core.ir import Model

MAX_BYTES = 1_000_000


class ImportDataset(Model):
    project_id: str
    format: Literal["csv", "json"]
    content: str = Field(max_length=MAX_BYTES)
    filename: str = Field(default="dataset.json", max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    column_types: dict[str, Literal["string", "integer", "number", "boolean", "json"]] = Field(
        default_factory=dict, max_length=100
    )


def validate_rows(rows):
    if not 1 <= len(rows) <= 100 or not isinstance(rows[0], dict) or not rows[0]:
        raise ValueError("A dataset requires 1–100 nonempty object rows")
    columns = list(rows[0])
    if len(columns) > 100:
        raise ValueError("A dataset may contain at most 100 columns")
    remaining = 20000

    def check(value, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 16:
            raise ValueError("Dataset nesting or value count exceeds limits")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Dataset numbers must be finite")
        if isinstance(value, str) and len(value) > 100000:
            raise ValueError("Dataset strings may contain at most 100000 characters")
        if isinstance(value, dict):
            for key, child in value.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or len(key) > 100
                    or any(ord(c) < 32 for c in key)
                ):
                    raise ValueError(
                        "Dataset keys must be nonempty strings without control characters"
                    )
                check(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                check(child, depth + 1)

    for row in rows:
        if not isinstance(row, dict) or set(row) != set(columns):
            raise ValueError("All dataset rows must have the same columns")
        if any(key.startswith("secret.") for key in row):
            raise ValueError("Dataset columns cannot use the reserved secret. prefix")
        check(row)
    if len(json.dumps(rows, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_BYTES:
        raise ValueError("Dataset content exceeds 1 MB")
    return columns


def strict_json(content):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON object keys are not allowed")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("JSON numbers must be finite")

    try:
        return json.loads(content, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Invalid or excessively nested JSON dataset") from exc


def convert(value, kind):
    if kind == "string":
        return value
    if kind == "integer":
        return int(value, 10)
    if kind == "number":
        return float(value)
    if kind == "boolean":
        if value.lower() not in {"true", "false"}:
            raise ValueError("Boolean cells must be true or false")
        return value.lower() == "true"
    return strict_json(value)


def parse_dataset(value):
    raw = value.content.encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("Dataset file exceeds 1 MB")
    content = value.content.removeprefix("\ufeff")
    if value.format == "json":
        if value.column_types:
            raise ValueError("JSON datasets retain native types; column_types applies only to CSV")
        rows = strict_json(content)
        if not isinstance(rows, list):
            raise ValueError("JSON dataset must be an array of objects")
    else:
        reader = csv.reader(io.StringIO(content, newline=""), strict=True)
        try:
            columns = next(reader, [])
            if not columns or len(set(columns)) != len(columns):
                raise ValueError("CSV requires unique, nonempty column headers")
            if set(value.column_types) - set(columns):
                raise ValueError("Column types must refer to existing CSV headers")
            rows = []
            for line_number, cells in enumerate(reader, 2):
                if not cells:
                    continue
                if len(cells) != len(columns):
                    raise ValueError(f"CSV row {line_number} has a different column count")
                try:
                    row = {
                        key: convert(cell, value.column_types.get(key, "string"))
                        for key, cell in zip(columns, cells, strict=True)
                    }
                except ValueError as exc:
                    raise ValueError(f"CSV row {line_number} has an invalid typed cell") from exc
                rows.append(row)
                if len(rows) > 100:
                    raise ValueError("A dataset may contain at most 100 rows")
        except csv.Error as exc:
            raise ValueError("Invalid CSV dataset") from exc
    columns = validate_rows(rows)
    filename = value.filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not filename or filename in {".", ".."}:
        raise ValueError("Dataset filename is invalid")
    return {
        "name": value.name or filename.rsplit(".", 1)[0],
        "rows": rows,
        "source": {
            "format": value.format,
            "filename": filename,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "column_types": value.column_types,
        },
    }, columns


def export_dataset(body, format):
    if format == "json":
        return json.dumps(body["rows"], ensure_ascii=False, indent=2), "application/json"
    if format != "csv":
        raise ValueError("Dataset export format must be json or csv")
    buffer = io.StringIO(newline="")
    columns = list(body["rows"][0])
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in body["rows"]:
        writer.writerow(
            {
                key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list, bool)) or value is None
                else value
                for key, value in row.items()
            }
        )
    return buffer.getvalue(), "text/csv; charset=utf-8"
