"""PostgreSQL / Database query tracing and cursor wrapper helpers."""

import logging
import re
from typing import Any, Callable, Dict, Optional, Tuple

from opentelemetry.trace import SpanKind, StatusCode, get_tracer

from tracenest.config import SDKConfig
from tracenest.sanitize import sanitize_sql
from tracenest.tracing import reentrant_guard, traced_span
import tracenest

logger = logging.getLogger("tracenest.integrations.postgres")

_config: Optional[SDKConfig] = None


def set_config(config: Optional[SDKConfig]) -> None:
    """Set the active SDKConfig for this module via the integration seam."""
    global _config
    _config = config


def _get_config() -> Optional[SDKConfig]:
    """Return the config received through the integration seam, or global fallback."""
    return _config if _config is not None else tracenest.get_config()

_KNOWN_SQL_OPS = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "CREATE",
    "ALTER",
    "DROP",
    "TRUNCATE",
    "SET",
    "SHOW",
    "EXPLAIN",
)

_TABLE_RE = re.compile(r'\b(?:FROM|INTO|UPDATE|TABLE)\s+["`]?([a-zA-Z0-9_]+)["`]?', re.IGNORECASE)


def extract_operation(sql: Optional[str]) -> str:
    """Extract SQL operation keyword (e.g. SELECT, INSERT) from statement."""
    if not sql:
        return "QUERY"
    words = str(sql).strip().split()
    if words:
        op = words[0].upper()
        if op in _KNOWN_SQL_OPS:
            return op
    return "QUERY"


def extract_query_summary(sql: Optional[str]) -> str:
    """Extract a concise query summary (e.g. 'SELECT api_product', 'UPDATE api_product')."""
    if not sql:
        return "QUERY"
    cleaned = str(sql).strip()
    op = extract_operation(cleaned)
    match = _TABLE_RE.search(cleaned)
    if match:
        table = match.group(1).replace('"', '').replace('`', '')
        return f"{op} {table}"
    words = cleaned.split()
    if len(words) >= 2 and words[0].upper() in _KNOWN_SQL_OPS:
        return f"{words[0].upper()} {words[1]}"
    return op


def _extract_django_db_meta(instance: Any) -> Tuple[str, str, str, str, int, Optional[str], str, str]:
    """Extract connection parameters and role from Django's db connection on CursorWrapper.
    
    Returns:
        (db_alias, db_vendor, db_name, db_host, db_port, db_user, db_role, peer_service)
    """
    db_conn = getattr(instance, "db", None)
    db_alias = "default"
    db_vendor = "postgresql"
    db_name = "unknown"
    db_host = "localhost"
    db_port = 5432
    db_user = None

    if db_conn is not None:
        db_alias = getattr(db_conn, "alias", "default")
        db_vendor = getattr(db_conn, "vendor", "postgresql")
        settings_dict = getattr(db_conn, "settings_dict", {})
        if isinstance(settings_dict, dict):
            db_name = settings_dict.get("NAME") or "unknown"
            db_host = settings_dict.get("HOST") or "localhost"
            raw_port = settings_dict.get("PORT")
            db_user = settings_dict.get("USER") or None
            try:
                db_port = int(raw_port) if raw_port else 5432
            except (ValueError, TypeError):
                db_port = 5432

    alias_str = str(db_alias).lower()
    if "slave" in alias_str or "replica" in alias_str or "read" in alias_str:
        db_role = "replica"
    else:
        db_role = "primary"

    peer_service = f"postgres-{db_alias}" if db_alias and db_alias != "default" else "postgres"

    return db_alias, db_vendor, db_name, db_host, db_port, db_user, db_role, peer_service




def is_pgbouncer_connection(db_host: Any, db_port: Any) -> bool:
    """Determine if database connection is routed through PgBouncer."""
    host_str = str(db_host).lower() if db_host else ""
    try:
        port_num = int(db_port) if db_port else 0
    except (ValueError, TypeError):
        port_num = 0
    return host_str == "pgbouncer" or port_num == 6432 or "pgbouncer" in host_str


from contextlib import contextmanager
try:
    from opentelemetry.instrumentation.utils import _SUPPRESS_INSTRUMENTATION_KEY
except ImportError:
    from opentelemetry.context import _SUPPRESS_INSTRUMENTATION_KEY
from opentelemetry.context import attach, detach, set_value

@contextmanager
def suppress_db_instrumentation():
    token = attach(set_value(_SUPPRESS_INSTRUMENTATION_KEY, True))
    try:
        yield
    finally:
        detach(token)


def traced_django_cursor_exec(
    wrapped: Callable,
    instance: Any,
    args: Any,
    kwargs: Any,
    op_type: str = "execute",
) -> Any:
    """Wrapper for Django CursorWrapper.execute and executemany."""
    # Re-entrancy guard to avoid nested spans for the same logical query
    with reentrant_guard(instance, "_tp_in_exec") as should_trace:
        if not should_trace:
            return wrapped(*args, **kwargs)

        tracer = get_tracer("tracenest.postgres")
        sql = args[0] if args else kwargs.get("sql", "")
        sanitized_sql = sanitize_sql(sql)
        op = extract_operation(sanitized_sql)

        db_alias, db_vendor, db_name, db_host, db_port, db_user, db_role, peer_service = _extract_django_db_meta(instance)

        summary = extract_query_summary(sanitized_sql)
        is_pgbouncer = is_pgbouncer_connection(db_host, db_port)

        span_attrs: Dict[str, Any] = {
            "db.system": db_vendor if db_vendor else "postgresql",
            "db.system.name": db_vendor if db_vendor else "postgresql",
            "peer.service": "pgbouncer" if is_pgbouncer else peer_service,
            "db.name": str(db_name),
            "db.namespace": str(db_name),
            "db.instance": str(db_alias),
            "db.connection_alias": str(db_alias),
            "net.peer.name": str(db_host),
            "net.peer.port": db_port,
            "server.address": str(db_host),
            "server.port": db_port,
            "db.statement": sanitized_sql,
            "db.query.text": sanitized_sql,
            "db.query.summary": summary,
            "db.operation": op,
            "db.operation.name": op,
            "db.role": db_role,
            "resource.name": sanitized_sql,
        }
        if is_pgbouncer:
            span_attrs["db.connection.pool"] = "pgbouncer"
        if db_user:
            span_attrs["db.user"] = str(db_user)

        db_icon = "🔵" if is_pgbouncer else "🐘"

        span_name = f"{db_icon} {sanitized_sql}" if sanitized_sql else f"{db_icon} postgres.query"
        with traced_span(
            span_name,
            kind=SpanKind.CLIENT,
            attributes=span_attrs,
            tracer_name="tracenest.postgres",
        ) as span:
            try:
                with suppress_db_instrumentation():
                    result = wrapped(*args, **kwargs)
                cursor = getattr(instance, "cursor", instance)
                rowcount = getattr(cursor, "rowcount", None)
                if rowcount is not None and rowcount >= 0:
                    span.set_attribute("db.row_count", rowcount)
                    span.set_attribute("db.response.returned_rows", rowcount)
                return result
            except Exception as exc:
                span.record_exception(exc)
                span.set_attribute("error", True)
                span.set_attribute("error.type", exc.__class__.__name__)
                span.set_status(StatusCode.ERROR, description=str(exc))
                raise






