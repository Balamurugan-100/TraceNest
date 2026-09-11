"""PostgresIntegration — wraps Django DB cursors and psycopg2/psycopg driver cursors."""

import importlib
import logging

from tracenest.integrations.base import BaseIntegration
from .cursor import traced_django_cursor_exec

logger = logging.getLogger("tracenest.integrations.postgres")


class PostgresIntegration(BaseIntegration):
    """Deep SQL query tracing for PostgreSQL and Django database backends."""

    name = "postgres"

    def is_installed(self) -> bool:
        has_django_db = False
        try:
            importlib.import_module("django.db.backends.utils")
            has_django_db = True
        except ImportError:
            pass

        has_psycopg2 = False
        try:
            importlib.import_module("psycopg2")
            has_psycopg2 = True
        except ImportError:
            pass

        has_psycopg3 = False
        try:
            importlib.import_module("psycopg")
            has_psycopg3 = True
        except ImportError:
            pass

        return has_django_db or has_psycopg2 or has_psycopg3

    def _apply_patch(self) -> None:
        from . import cursor as _cursor
        _cursor.set_config(self._config)

        # 1. Patch Django database wrappers (django.db.backends.utils.CursorWrapper)
        try:
            import django.db.backends.utils

            self.wrap(
                "django.db.backends.utils.CursorWrapper",
                "execute",
                lambda w, i, a, k: traced_django_cursor_exec(w, i, a, k, "execute"),
            )
            self.wrap(
                "django.db.backends.utils.CursorWrapper",
                "executemany",
                lambda w, i, a, k: traced_django_cursor_exec(w, i, a, k, "executemany"),
            )

            if hasattr(django.db.backends.utils, "CursorDebugWrapper"):
                self.wrap(
                    "django.db.backends.utils.CursorDebugWrapper",
                    "execute",
                    lambda w, i, a, k: traced_django_cursor_exec(w, i, a, k, "execute"),
                )
                self.wrap(
                    "django.db.backends.utils.CursorDebugWrapper",
                    "executemany",
                    lambda w, i, a, k: traced_django_cursor_exec(w, i, a, k, "executemany"),
                )
        except Exception as exc:
            logger.debug("Django db backends utils patch skipped: %s", exc)

        # 2. Instrument direct psycopg2 driver via official OTel Psycopg2Instrumentor
        try:
            from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor

            instrumentor = Psycopg2Instrumentor()
            if not instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.instrument()
        except Exception as exc:
            logger.debug("Psycopg2Instrumentor patch skipped: %s", exc)

    def uninstrument(self) -> bool:
        try:
            from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor

            instrumentor = Psycopg2Instrumentor()
            if instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.uninstrument()
        except Exception:
            pass

        return super().uninstrument()

