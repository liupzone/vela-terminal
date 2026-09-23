"""A tiny TOML reader/writer covering the subset Vela's config file needs.

Ubuntu 20.04 ships Python 3.8, which has no ``tomllib``, and we refuse to add a
third-party dependency to a terminal emulator.  Supported syntax:

* comments with ``#``
* ``[section]`` and ``[section.sub]`` tables
* bare keys and quoted keys
* strings (basic and literal), integers, floats, booleans
* single-line arrays (including nested arrays of strings/numbers)

Anything outside that subset raises :class:`TomlError` with a line number, so a
hand-edited config fails loudly instead of silently reverting.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


class TomlError(ValueError):
    """Raised when a config file uses syntax this reader does not support."""

    def __init__(self, message: str, line_no: int = 0) -> None:
        self.line_no = line_no
        prefix = f"line {line_no}: " if line_no else ""
        super().__init__(prefix + message)


_BARE_KEY = re.compile(r"^[A-Za-z0-9_\-]+$")
_INT = re.compile(r"^[+-]?\d+$")
_FLOAT = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_BOOL = {"true": True, "false": False}


def _split_comment(line: str) -> str:
    """Strip a trailing ``#`` comment, ignoring ``#`` inside quoted strings."""
    out: List[str] = []
    quote: str = ""
    escaped = False
    for ch in line:
        if quote:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


def _split_top_level(text: str, line_no: int) -> List[str]:
    """Split ``a, b, [c, d]`` on commas that are not nested or quoted."""
    parts: List[str] = []
    depth = 0
    quote = ""
    escaped = False
    current: List[str] = []
    for ch in text:
        if quote:
            current.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch in "[{":
            depth += 1
            current.append(ch)
        elif ch in "]}":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if depth != 0 or quote:
        raise TomlError("unbalanced brackets or quotes in value", line_no)
    tail = "".join(current)
    if tail.strip() or parts:
        parts.append(tail)
    return parts


def _parse_string(token: str, line_no: int) -> str:
    if len(token) < 2 or token[0] != token[-1]:
        raise TomlError(f"unterminated string: {token!r}", line_no)
    body = token[1:-1]
    if token[0] == "'":
        return body
    out: List[str] = []
    escaped = False
    for ch in body:
        if escaped:
            out.append(
                {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}.get(ch, ch)
            )
            escaped = False
        elif ch == "\\":
            escaped = True
        else:
            out.append(ch)
    return "".join(out)


def _parse_value(token: str, line_no: int) -> Any:
    token = token.strip()
    if not token:
        raise TomlError("missing value", line_no)
    if token[0] in "\"'":
        return _parse_string(token, line_no)
    if token.startswith("["):
        if not token.endswith("]"):
            raise TomlError(f"unterminated array: {token!r}", line_no)
        inner = token[1:-1].strip()
        if not inner:
            return []
        return [_parse_value(part, line_no) for part in _split_top_level(inner, line_no)]
    lowered = token.lower()
    if lowered in _BOOL:
        return _BOOL[lowered]
    if _INT.match(token):
        return int(token)
    if _FLOAT.match(token):
        return float(token)
    raise TomlError(f"unsupported value: {token!r}", line_no)


def loads(text: str) -> Dict[str, Any]:
    """Parse TOML ``text`` into a nested dict."""
    root: Dict[str, Any] = {}
    current: Dict[str, Any] = root
    for index, raw in enumerate(text.splitlines(), start=1):
        line = _split_comment(raw).strip()
        if not line:
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                raise TomlError("unterminated table header", index)
            path = line[1:-1].strip()
            if not path:
                raise TomlError("empty table name", index)
            current = root
            for part in path.split("."):
                part = part.strip()
                if not part:
                    raise TomlError(f"bad table name: {path!r}", index)
                if part[0] in "\"'":
                    part = _parse_string(part, index)
                elif not _BARE_KEY.match(part):
                    raise TomlError(f"unsupported table name: {part!r}", index)
                existing = current.get(part)
                if existing is None:
                    existing = {}
                    current[part] = existing
                if not isinstance(existing, dict):
                    raise TomlError(f"table {part!r} conflicts with a value", index)
                current = existing
            continue
        if "=" not in line:
            raise TomlError(f"expected key = value, got {line!r}", index)
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key[0] in "\"'":
            key = _parse_string(key, index)
        elif not _BARE_KEY.match(key):
            raise TomlError(f"unsupported key: {key!r}", index)
        current[key] = _parse_value(value, index)
    return root


def load(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return loads(handle.read())


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = (
            value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        )
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    raise TypeError(f"cannot serialise {type(value).__name__} to TOML")


def dumps(data: Dict[str, Any], header: str = "") -> str:
    """Serialise a nested dict; scalars first, then subtables."""
    lines: List[str] = []
    if header:
        lines.extend(f"# {part}" if part else "#" for part in header.splitlines())
        lines.append("")
    _dump_table(data, [], lines)
    return "\n".join(lines).rstrip("\n") + "\n"


def _dump_table(data: Dict[str, Any], path: List[str], lines: List[str]) -> None:
    scalars: List[Tuple[str, Any]] = []
    tables: List[Tuple[str, Dict[str, Any]]] = []
    for key, value in data.items():
        if isinstance(value, dict):
            tables.append((key, value))
        else:
            scalars.append((key, value))
    if path and (scalars or not tables):
        lines.append("[" + ".".join(path) + "]")
    for key, value in scalars:
        lines.append(f"{key} = {_format_value(value)}")
    if path and scalars:
        lines.append("")
    for key, table in tables:
        _dump_table(table, path + [key], lines)
