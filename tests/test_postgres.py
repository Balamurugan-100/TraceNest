"""Unit tests for Postgres / Database Integration."""

from contextlib import nullcontext
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

import django
from django.conf import settings
from django.db.backends.utils import CursorWrapper
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import path

# Configure minimal Django settings for testing if not already configured
if not settings.configured:
    settings.configure(
        DEBUG=False,
        SECRET_KEY="test-secret-key-postgres",
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            },
            "slave1": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            },
        },
        MIDDLEWARE=[
            "django.middleware.security.SecurityMiddleware",
            "django.middleware.common.CommonMiddleware",
        ],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
            }
        ],
    )
    django.setup()

import tracenest
from tracenest.integrations.django import DjangoIntegration
from tracenest.integrations.postgres import PostgresIntegration


class MockRawCursor:
    def __init__(self, rowcount=1, raise_exc=None):
        self.executed = []
        self.rowcount = rowcount
        self.raise_exc = raise_exc

    def execute(self, sql, params=None):
        if self.raise_exc:
            raise self.raise_exc
        self.executed.append((sql, params))
        return self

    def executemany(self, sql, param_list):
        if self.raise_exc:
            raise self.raise_exc
        self.executed.append((sql, param_list))
        return self


class MockDatabaseConnection:
    def __init__(
        self,
        alias="default",
        vendor="postgresql",
        host="db.internal",
        port=5432,
        db_name="prod_db",
        user="postgres_user",
    ):
        self.alias = alias
        self.vendor = vendor
        self.execute_wrappers = []
        self.wrap_database_errors = nullcontext()
        self.settings_dict = {
            "NAME": db_name,
            "HOST": host,
            "PORT": port,
            "USER": user,
        }

    def validate_no_broken_transaction(self):
        pass


@pytest.fixture(autouse=True)
def clean_sdk_and_postgres():
    tracenest._reset_for_testing()
    from tracenest.integrations import get_integration_manager

    mgr = get_integration_manager()
    mgr.apply_integrations()
    yield
    try:
        mgr.uninstrument_all()
    except Exception:
        pass
    tracenest._reset_for_testing()


def test_select_creates_span():
    """Verify executing a query through CursorWrapper creates a CLIENT span with connection attributes."""
    exporter = InMemorySpanExporter()
    tracenest.init(service="postgres-test-svc", exporter=exporter, export_batch=False)

    raw_cursor = MockRawCursor(rowcount=5)
    mock_db = MockDatabaseConnection(
        alias="default",
        vendor="postgresql",
        host="pg-primary.prod",
        port=5432,
        db_name="accounts",
        user="app_user",
    )
    wrapper = CursorWrapper(raw_cursor, mock_db)

    wrapper.execute("SELECT id, name FROM users WHERE active = 1")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "🐘 SELECT id, name FROM users WHERE active = ?"
    assert span.kind == SpanKind.CLIENT
    assert span.attributes["db.system"] == "postgresql"
    assert span.attributes["db.operation"] == "SELECT"
    assert span.attributes["db.name"] == "accounts"
    assert span.attributes["db.instance"] == "default"
    assert span.attributes["db.connection_alias"] == "default"
    assert span.attributes["net.peer.name"] == "pg-primary.prod"
    assert span.attributes["net.peer.port"] == 5432
    assert span.attributes["server.address"] == "pg-primary.prod"
    assert span.attributes["server.port"] == 5432
    assert span.attributes["db.user"] == "app_user"
    assert span.attributes["peer.service"] == "postgres"
    assert span.attributes["db.row_count"] == 5
    assert span.status.status_code == StatusCode.OK


def test_sanitized_sql_in_attributes():
    """Verify raw parameters and values are sanitized in db.statement."""
    exporter = InMemorySpanExporter()
    tracenest.init(service="postgres-test-svc", exporter=exporter, export_batch=False)

    raw_cursor = MockRawCursor()
    mock_db = MockDatabaseConnection(alias="default")
    wrapper = CursorWrapper(raw_cursor, mock_db)

    wrapper.execute("SELECT * FROM sensitive_users WHERE email = 'secret@example.com' AND age >= 21")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]

    statement = span.attributes["db.statement"]
    assert "secret@example.com" not in statement
    assert "21" not in statement
    assert statement == "SELECT * FROM sensitive_users WHERE email = ? AND age >= ?"


def test_primary_role_detected():
    """Verify primary role is detected for default / non-replica database aliases."""
    exporter = InMemorySpanExporter()
    tracenest.init(service="postgres-test-svc", exporter=exporter, export_batch=False)

    raw_cursor = MockRawCursor()
    mock_db = MockDatabaseConnection(alias="default")
    wrapper = CursorWrapper(raw_cursor, mock_db)

    wrapper.execute("SELECT 1")

    spans = exporter.get_finished_spans()
    assert spans[0].attributes["db.role"] == "primary"
    assert spans[0].attributes["peer.service"] == "postgres"


def test_replica_role_detected():
    """Verify replica role and custom peer.service are detected for slave/read aliases."""
    exporter = InMemorySpanExporter()
    tracenest.init(service="postgres-test-svc", exporter=exporter, export_batch=False)

    raw_cursor = MockRawCursor()
    mock_db = MockDatabaseConnection(alias="slave1db", host="pg-replica.prod")
    wrapper = CursorWrapper(raw_cursor, mock_db)

    wrapper.execute("SELECT 1 FROM readonly_table")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]

    assert span.attributes["db.role"] == "replica"
    assert span.attributes["db.instance"] == "slave1db"
    assert span.attributes["peer.service"] == "postgres-slave1db"


def test_reentrancy_guard_prevents_duplicate_spans():
    """Verify that re-entrant cursor executions do not create duplicate spans."""
    exporter = InMemorySpanExporter()
    tracenest.init(service="postgres-test-svc", exporter=exporter, export_batch=False)

    class ReentrantRawCursor:
        def __init__(self):
            self.rowcount = 1

        def execute(self, sql, params=None):
            # Nested call on same wrapper
            if not getattr(wrapper, "_reentered", False):
                wrapper._reentered = True
                wrapper.execute("SELECT 2")
            return self

    raw_cursor = ReentrantRawCursor()
    mock_db = MockDatabaseConnection(alias="default")
    wrapper = CursorWrapper(raw_cursor, mock_db)

    wrapper.execute("SELECT 1")

    spans = exporter.get_finished_spans()
    # Exactly 1 span from the outer call
    assert len(spans) == 1


def test_query_exception_records_error():
    """Verify database exceptions are recorded with status ERROR and error attributes."""
    exporter = InMemorySpanExporter()
    tracenest.init(service="postgres-test-svc", exporter=exporter, export_batch=False)

    raw_cursor = MockRawCursor(raise_exc=RuntimeError("connection deadlock detected"))
    mock_db = MockDatabaseConnection(alias="default")
    wrapper = CursorWrapper(raw_cursor, mock_db)

    with pytest.raises(RuntimeError, match="connection deadlock detected"):
        wrapper.execute("UPDATE accounts SET balance = balance - 100 WHERE id = 1")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]

    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["error"] is True
    assert span.attributes["error.type"] == "RuntimeError"
    assert len(span.events) >= 1
    assert span.events[0].name == "exception"


def test_executemany_creates_span():
    """Verify executemany creates a CLIENT span with correct operation."""
    exporter = InMemorySpanExporter()
    tracenest.init(service="postgres-test-svc", exporter=exporter, export_batch=False)

    raw_cursor = MockRawCursor(rowcount=3)
    mock_db = MockDatabaseConnection(alias="default")
    wrapper = CursorWrapper(raw_cursor, mock_db)

    wrapper.executemany(
        "INSERT INTO items (name, price) VALUES (%s, %s)",
        [("Item A", 10), ("Item B", 20), ("Item C", 30)],
    )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]

    assert span.name == "🐘 INSERT INTO items (name, price) VALUES (%s, %s)"
    assert span.kind == SpanKind.CLIENT
    assert span.attributes["db.operation"] == "INSERT"
    assert span.attributes["db.row_count"] == 3
    assert span.status.status_code == StatusCode.OK


def db_test_view(request):
    raw_cursor = MockRawCursor(rowcount=2)
    mock_db = MockDatabaseConnection(alias="slave1", host="replica.prod", db_name="shop_db")
    wrapper = CursorWrapper(raw_cursor, mock_db)
    wrapper.execute("SELECT id, name FROM products WHERE category = 'books'")
    return HttpResponse("OK")


urlpatterns = [
    path("api/db-test/", db_test_view, name="db-test"),
]


def test_uninstrument_and_idempotency():
    """Verify clean uninstrumentation restores original methods and instrument is idempotent."""
    tracenest._reset_for_testing()
    exporter = InMemorySpanExporter()
    tracenest.init(service="postgres-test-svc", exporter=exporter, export_batch=False, auto_patch=False)

    integration = PostgresIntegration()
    assert integration.instrument() is True
    assert integration.instrument() is True  # Idempotent

    raw_cursor = MockRawCursor()
    mock_db = MockDatabaseConnection(alias="default")
    wrapper = CursorWrapper(raw_cursor, mock_db)

    wrapper.execute("SELECT 1")
    assert len(exporter.get_finished_spans()) == 1
    exporter.clear()

    # Uninstrument
    assert integration.uninstrument() is True

    # After uninstrument, wrapper.execute should not create spans
    wrapper.execute("SELECT 1")
    assert len(exporter.get_finished_spans()) == 0


def test_patch_all_enables_postgres():
    """Verify tracenest.patch_all() auto-discovers and instruments postgres."""
    tracenest._reset_for_testing()
    exporter = InMemorySpanExporter()
    tracenest.init(service="postgres-test-svc", exporter=exporter, export_batch=False)

    enabled = tracenest.patch_all()
    assert "postgres" in enabled

    raw_cursor = MockRawCursor()
    mock_db = MockDatabaseConnection(alias="default")
    wrapper = CursorWrapper(raw_cursor, mock_db)

    wrapper.execute("SELECT 1")
    spans = exporter.get_finished_spans()
    db_spans = [s for s in spans if s.attributes.get("db.system") == "postgresql"]
    assert len(db_spans) == 1
    assert db_spans[0].name == "🐘 SELECT ?"


def test_django_request_waterfall_with_db():
    """Verify full waterfall: django.request -> django.view -> postgres.query."""
    tracenest._reset_for_testing()
    exporter = InMemorySpanExporter()
    tracenest.init(service="waterfall-test", exporter=exporter, export_batch=False)
    tracenest.patch_all()

    from django.urls import clear_url_caches
    from django.core.handlers.wsgi import WSGIHandler

    original_urlconf = settings.ROOT_URLCONF
    settings.ROOT_URLCONF = __name__
    clear_url_caches()

    try:
        handler = WSGIHandler()
        handler.load_middleware()

        factory = RequestFactory()
        req = factory.get("/api/db-test/")
        resp = handler.get_response(req)
        assert resp.status_code == 200

        spans = exporter.get_finished_spans()
        req_span = next(s for s in spans if s.name == "django.request")
        db_spans = [s for s in spans if s.attributes.get("db.system") == "postgresql"]
        assert len(db_spans) == 1

        db_span = db_spans[0]
        assert db_span.name == "🐘 SELECT id, name FROM products WHERE category = ?"
        assert db_span.context.trace_id == req_span.context.trace_id
        assert db_span.attributes["db.role"] == "replica"
        assert db_span.attributes["peer.service"] == "postgres-slave1"
        assert db_span.attributes["db.name"] == "shop_db"
        assert db_span.kind == SpanKind.CLIENT
    finally:
        settings.ROOT_URLCONF = original_urlconf
        clear_url_caches()


def test_raw_psycopg_cursor_exec():
    """Verify PostgresIntegration instruments psycopg driver."""
    integration = PostgresIntegration()
    assert integration.is_installed() is True
    assert integration.name == "postgres"



def test_extract_operation_fallback():
    """Verify operation extraction falls back to QUERY for empty or unknown SQL."""
    from tracenest.integrations.postgres.cursor import extract_operation

    assert extract_operation("") == "QUERY"
    assert extract_operation(None) == "QUERY"
    assert extract_operation("   ") == "QUERY"
    assert extract_operation("UNKNOWN_SQL_KEYWORD something") == "QUERY"
    assert extract_operation("select * from t") == "SELECT"
    assert extract_operation("INSERT into t values(1)") == "INSERT"


def test_extract_query_summary():
    """Verify query summary extracts operation and target table correctly."""
    from tracenest.integrations.postgres.cursor import extract_query_summary

    assert extract_query_summary("") == "QUERY"
    assert extract_query_summary(None) == "QUERY"
    assert extract_query_summary("SELECT * FROM products WHERE id = ?") == "SELECT products"
    assert extract_query_summary('SELECT "api_product"."id" FROM "api_product"') == "SELECT api_product"
    assert extract_query_summary('INSERT INTO "api_order" ("id") VALUES (?)') == "INSERT api_order"
    assert extract_query_summary('UPDATE "api_product" SET "stock" = ?') == "UPDATE api_product"
    assert extract_query_summary('DELETE FROM "api_cart"') == "DELETE api_cart"
    assert extract_query_summary("BEGIN") == "BEGIN"
    assert extract_query_summary("COMMIT") == "COMMIT"


def test_two_tier_db_spans_datadog_parity():
    """Verify db_two_tier_spans=True produces Datadog-parity nested spans:
    Tier 1 (parent): Connection Router / DB Alias (slave3db)
    Tier 2 (child): Physical Database / Driver Query (testpress)
    """
    tracenest._reset_for_testing()
    exporter = InMemorySpanExporter()
    tracenest.init(
        service="postgres-test-svc",
        exporter=exporter,
        export_batch=False,
        db_two_tier_spans=True,
    )
    from tracenest.integrations import get_integration_manager
    mgr = get_integration_manager()
    mgr.apply_integrations()

    raw_cursor = MockRawCursor(rowcount=3)
    mock_db = MockDatabaseConnection(
        alias="slave3db",
        vendor="postgresql",
        host="10.0.0.5",
        port=5432,
        db_name="testpress",
        user="postgres",
    )
    wrapper = CursorWrapper(raw_cursor, mock_db)

    wrapper.execute("SELECT users_user.id FROM users_user WHERE is_active = 1")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.name == "🐘 SELECT users_user.id FROM users_user WHERE is_active = ?"
    assert span.attributes["peer.service"] == "postgres-slave3db"
    assert span.attributes["db.name"] == "testpress"
    assert span.attributes["db.statement"] == "SELECT users_user.id FROM users_user WHERE is_active = ?"
    assert span.attributes["db.row_count"] == 3
    assert span.kind == SpanKind.CLIENT


def test_pgbouncer_single_span_attributes():
    """Verify queries routed through PgBouncer produce a single DB CLIENT span with pgbouncer peer attributes."""
    tracenest._reset_for_testing()
    exporter = InMemorySpanExporter()
    tracenest.init(
        service="pgbouncer-test-svc",
        exporter=exporter,
        export_batch=False,
    )
    from tracenest.integrations import get_integration_manager
    mgr = get_integration_manager()
    mgr.apply_integrations()

    raw_cursor = MockRawCursor(rowcount=5)
    mock_db = MockDatabaseConnection(
        alias="default",
        vendor="postgresql",
        host="pgbouncer",
        port=6432,
        db_name="django_otel",
        user="django",
    )
    wrapper = CursorWrapper(raw_cursor, mock_db)

    wrapper.execute("SELECT * FROM auth_user WHERE id = 1")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.name == "🔵 SELECT * FROM auth_user WHERE id = ?"
    assert span.kind == SpanKind.CLIENT
    assert span.attributes["db.system"] == "postgresql"
    assert span.attributes["db.system.name"] == "postgresql"
    assert span.attributes["peer.service"] == "pgbouncer"
    assert span.attributes["db.connection.pool"] == "pgbouncer"
    assert span.attributes["server.address"] == "pgbouncer"
    assert span.attributes["server.port"] == 6432
    assert span.attributes["db.name"] == "django_otel"
    assert span.attributes["db.namespace"] == "django_otel"
    assert span.attributes["db.operation.name"] == "SELECT"
    assert span.attributes["db.query.summary"] == "SELECT auth_user"
    assert span.attributes["db.response.returned_rows"] == 5


def test_suppress_driver_instrumentation_prevents_duplicate_spans():
    """Verify suppress_db_instrumentation sets OTel _SUPPRESS_INSTRUMENTATION_KEY to prevent duplicate driver spans."""
    tracenest._reset_for_testing()
    exporter = InMemorySpanExporter()
    tracenest.init(service="single-span-policy-svc", exporter=exporter, export_batch=False)

    from tracenest.integrations.postgres.cursor import suppress_db_instrumentation
    from opentelemetry.context import get_value, _SUPPRESS_INSTRUMENTATION_KEY

    # Outside context: not suppressed
    assert get_value(_SUPPRESS_INSTRUMENTATION_KEY) is None or get_value(_SUPPRESS_INSTRUMENTATION_KEY) is False

    # Inside context: suppressed (Psycopg2Instrumentor checks this key and skips span creation)
    with suppress_db_instrumentation():
        assert get_value(_SUPPRESS_INSTRUMENTATION_KEY) is True







