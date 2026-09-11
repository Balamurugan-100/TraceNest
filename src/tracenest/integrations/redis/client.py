"""Redis command and pipeline tracing wrappers."""

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from opentelemetry.trace import SpanKind

from tracenest.tracing import reentrant_guard, traced_span

logger = logging.getLogger("tracenest.integrations.redis")

_SENSITIVE_COMMANDS = {"AUTH"}
_MAX_STATEMENT_LEN = 2048
_MAX_ARG_LEN = 256

# Regex to detect and mask IPv4 and IPv6 addresses in cache keys and statements
_IPV4_RE = re.compile(r"(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])")
_IPV6_RE = re.compile(r"(?<![0-9a-fA-F:])(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}(?![0-9a-fA-F:])")


def _sanitize_string(s: str) -> str:
    """Sanitize IP addresses and sensitive patterns from string."""
    s = _IPV4_RE.sub("?", s)
    s = _IPV6_RE.sub("?", s)
    return s


def _format_arg(arg: Any) -> str:
    """Format an individual argument safely for tracing, masking IPs and truncating large values."""
    if isinstance(arg, bytes):
        try:
            s = arg.decode("utf-8", errors="replace")
        except Exception:
            s = str(arg)
    else:
        s = str(arg)

    s = _sanitize_string(s)

    if len(s) > _MAX_ARG_LEN:
        return s[:_MAX_ARG_LEN] + "..."
    return s


def format_redis_statement(args: Tuple[Any, ...]) -> Tuple[str, str, str]:
    """
    Format and sanitize Redis command arguments.
    
    Returns:
        (operation, statement, summary)
    """
    if not args:
        return "COMMAND", "COMMAND", "COMMAND"

    raw_op = args[0]
    if isinstance(raw_op, bytes):
        raw_op = raw_op.decode("utf-8", errors="replace")
    op = str(raw_op).upper()

    # Redact AUTH passwords
    if op in _SENSITIVE_COMMANDS:
        redacted_args = ["?"] * (len(args) - 1)
        statement = f"{op} " + " ".join(redacted_args) if redacted_args else op
        return op, statement.strip(), op

    # Redact sensitive CONFIG commands
    if op == "CONFIG" and len(args) >= 2:
        sub_cmd = _format_arg(args[1]).upper()
        if sub_cmd in ("SET", "REQUIREPASS", "MASTERAUTH") and len(args) > 2:
            statement = f"CONFIG {sub_cmd} ?"
            return op, statement, f"CONFIG {sub_cmd}"

    formatted_parts: List[str] = [op]
    for arg in args[1:]:
        formatted_parts.append(_format_arg(arg))

    statement = " ".join(formatted_parts)
    if len(statement) > _MAX_STATEMENT_LEN:
        statement = statement[:_MAX_STATEMENT_LEN] + " ... [truncated]"

    # Build concise summary (e.g. "GET user:?" or "SET :1:throttle_burst_test_?")
    key = _format_arg(args[1]) if len(args) > 1 else ""
    summary = f"{op} {key}".strip() if key else op

    return op, statement, summary


def _extract_redis_conn_meta(instance: Any) -> Tuple[str, int, int, str]:
    """
    Extract host, port, db_index, and peer_service from a Redis or Pipeline instance.
    
    Returns:
        (host, port, db_index, peer_service)
    """
    pool = getattr(instance, "connection_pool", None)
    host = "localhost"
    port = 6379
    db_index = 0

    if pool is not None:
        kwargs = getattr(pool, "connection_kwargs", {})
        if isinstance(kwargs, dict):
            host = kwargs.get("host") or kwargs.get("path") or "localhost"
            raw_port = kwargs.get("port", 6379)
            raw_db = kwargs.get("db", 0)
            try:
                port = int(raw_port) if raw_port is not None else 6379
            except (ValueError, TypeError):
                port = 6379
            try:
                db_index = int(raw_db) if raw_db is not None else 0
            except (ValueError, TypeError):
                db_index = 0
        else:
            host = getattr(pool, "host", "localhost") or "localhost"
            try:
                port = int(getattr(pool, "port", 6379) or 6379)
            except (ValueError, TypeError):
                port = 6379
            try:
                db_index = int(getattr(pool, "db", 0) or 0)
            except (ValueError, TypeError):
                db_index = 0

    peer_service = str(host) if host else "redis"
    return str(host), port, db_index, peer_service


def _build_span_attrs(
    host: str,
    port: int,
    db_index: int,
    peer_service: str,
    op: str,
    statement: str,
    summary: str,
    args_count: int,
) -> Dict[str, Any]:
    """Build standard OpenTelemetry DB attributes for Redis."""
    return {
        "db.system": "redis",
        "peer.service": peer_service,
        "db.name": str(db_index),
        "db.namespace": str(db_index),
        "db.instance": str(db_index),
        "db.redis.database_index": db_index,
        "net.peer.name": host,
        "net.peer.port": port,
        "server.address": host,
        "server.port": port,
        "db.operation": op,
        "db.operation.name": op,
        "db.statement": statement,
        "db.query.summary": summary,
        "db.redis.args_count": args_count,
        "resource.name": statement,
    }


def _command_meta(instance: Any, args: Any) -> Tuple[str, Dict[str, Any]]:
    """Build span name + attributes for a single Redis command."""
    op, statement, summary = format_redis_statement(args)
    host, port, db_index, peer_service = _extract_redis_conn_meta(instance)

    attrs = _build_span_attrs(
        host=host,
        port=port,
        db_index=db_index,
        peer_service=peer_service,
        op=op,
        statement=statement,
        summary=summary,
        args_count=len(args),
    )

    # Datadog APM parity: span name is the command verb (e.g. "GET", "SET", "HGET"),
    # while the statement and key are preserved in db.statement
    span_name = f"🔴 {op}" if op else "🔴 redis.command"
    return span_name, attrs


def _pipeline_meta(instance: Any) -> Tuple[str, Dict[str, Any]]:
    """Build span name + attributes for a Redis pipeline execution."""
    command_stack = getattr(instance, "command_stack", [])
    pipeline_len = len(command_stack) if command_stack is not None else 0

    is_transaction = getattr(instance, "transaction", False)
    op = "MULTI/EXEC" if is_transaction else "PIPELINE"

    # Collect command names from stack
    cmd_names: List[str] = []
    for cmd_item in (command_stack or []):
        try:
            if isinstance(cmd_item, (list, tuple)) and cmd_item:
                first = cmd_item[0]
                if isinstance(first, (list, tuple)) and first:
                    cmd_names.append(str(first[0]).upper())
                else:
                    cmd_names.append(str(first).upper())
        except Exception:
            pass

    if cmd_names:
        ops_summary = ", ".join(cmd_names[:10])
        if len(cmd_names) > 10:
            ops_summary += f", ... (+{len(cmd_names) - 10} more)"
        statement = f"{op} ({pipeline_len} commands): {ops_summary}"
    else:
        statement = f"{op} ({pipeline_len} commands)"

    summary = f"{op} [{pipeline_len}]"
    host, port, db_index, peer_service = _extract_redis_conn_meta(instance)

    attrs = _build_span_attrs(
        host=host,
        port=port,
        db_index=db_index,
        peer_service=peer_service,
        op=op,
        statement=statement,
        summary=summary,
        args_count=pipeline_len,
    )
    attrs["db.redis.pipeline_length"] = pipeline_len

    span_name = f"🔴 {statement}" if statement else "🔴 redis.pipeline"
    return span_name, attrs


def traced_redis_execute_command(
    wrapped: Callable,
    instance: Any,
    args: Any,
    kwargs: Any,
) -> Any:
    """Wrapper for redis.Redis.execute_command."""
    # Re-entrancy guard to avoid nested spans (e.g. from pipeline execution or internal redis calls)
    with reentrant_guard(instance, "_tp_in_exec") as should_trace:
        if not should_trace:
            return wrapped(*args, **kwargs)

        span_name, attrs = _command_meta(instance, args)
        with traced_span(
            span_name,
            kind=SpanKind.CLIENT,
            attributes=attrs,
            tracer_name="tracenest.redis",
        ):
            result = wrapped(*args, **kwargs)
            return result


def traced_pipeline_execute(
    wrapped: Callable,
    instance: Any,
    args: Any,
    kwargs: Any,
) -> Any:
    """Wrapper for redis.client.Pipeline.execute."""
    with reentrant_guard(instance, "_tp_in_exec") as should_trace:
        if not should_trace:
            return wrapped(*args, **kwargs)

        span_name, attrs = _pipeline_meta(instance)
        with traced_span(
            span_name,
            kind=SpanKind.CLIENT,
            attributes=attrs,
            tracer_name="tracenest.redis",
        ):
            result = wrapped(*args, **kwargs)
            return result


async def traced_async_redis_execute_command(
    wrapped: Callable,
    instance: Any,
    args: Any,
    kwargs: Any,
) -> Any:
    """Async wrapper for redis.asyncio.Redis.execute_command."""
    with reentrant_guard(instance, "_tp_in_exec") as should_trace:
        if not should_trace:
            return await wrapped(*args, **kwargs)

        span_name, attrs = _command_meta(instance, args)
        with traced_span(
            span_name,
            kind=SpanKind.CLIENT,
            attributes=attrs,
            tracer_name="tracenest.redis",
        ):
            result = await wrapped(*args, **kwargs)
            return result


async def traced_async_pipeline_execute(
    wrapped: Callable,
    instance: Any,
    args: Any,
    kwargs: Any,
) -> Any:
    """Async wrapper for redis.asyncio.client.Pipeline.execute."""
    with reentrant_guard(instance, "_tp_in_exec") as should_trace:
        if not should_trace:
            return await wrapped(*args, **kwargs)

        span_name, attrs = _pipeline_meta(instance)
        with traced_span(
            span_name,
            kind=SpanKind.CLIENT,
            attributes=attrs,
            tracer_name="tracenest.redis",
        ):
            result = await wrapped(*args, **kwargs)
            return result
