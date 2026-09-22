"""Shared pytest fixtures and Django configuration for TraceNest SDK tests."""

import django
from django.conf import settings
import pytest

import tracenest
from tracenest.integrations import get_integration_manager

urlpatterns = []

# Configure minimal Django settings once for all test suites
if not settings.configured:
    settings.configure(
        DEBUG=False,
        SECRET_KEY="test-secret-key-tracenest-global",
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
            "slave2": {
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


@pytest.fixture(autouse=True)
def clean_sdk_state():
    """Reset SDK and integration state before and after every test."""
    tracenest._reset_for_testing()
    mgr = get_integration_manager()
    mgr.apply_integrations()
    yield
    try:
        mgr.uninstrument_all()
    except Exception:
        pass
    tracenest._reset_for_testing()
