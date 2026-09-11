"""Sanitization utilities for URLs and SQL statements."""

import re
from typing import Optional
from urllib.parse import urlparse, urlunparse

_WHITESPACE_RE = re.compile(r"\s+")
_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")
# Match numeric literals not attached to word characters (e.g. '= 42', ', 123.45', 'IN (1, 2)')
_NUMERIC_LITERAL_RE = re.compile(r"(?<=[^\w\$.])\b\d+(?:\.\d+)?\b")
_MAX_STATEMENT_LENGTH = 4096


def sanitize_sql(sql: Optional[str], max_length: int = _MAX_STATEMENT_LENGTH) -> str:
    """
    Sanitize SQL statements by replacing literal values with placeholders.
    
    Replaces:
    - String literals: 'example' -> ?
    - Numeric literals: 42, 3.14 -> ?
    - Normalizes multiple whitespace characters into a single space.
    - Truncates excessively long queries.
    """
    if not sql:
        return ""
    if not isinstance(sql, str):
        try:
            sql = str(sql)
        except Exception:
            return ""

    # Replace string literals first
    sanitized = _STRING_LITERAL_RE.sub("?", sql)
    # Replace numeric literals
    sanitized = _NUMERIC_LITERAL_RE.sub("?", sanitized)
    # Normalize whitespace
    normalized = _WHITESPACE_RE.sub(" ", sanitized).strip()

    if len(normalized) > max_length:
        return normalized[:max_length] + " ... [truncated]"
    return normalized


def sanitize_url(url: Optional[str], strip_query: bool = False) -> str:
    """
    Sanitize a URL by stripping credentials (userinfo) and fragment.
    
    Optionally strips query parameters if strip_query is True.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        # Strip userinfo (username:password) if present
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = netloc.split("@")[-1]

        query = "" if strip_query else parsed.query

        cleaned = urlunparse((
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            query,
            "",  # strip fragment
        ))
        return cleaned
    except Exception:
        return str(url)
