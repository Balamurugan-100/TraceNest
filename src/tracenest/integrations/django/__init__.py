"""Django integration package — re-exports DjangoIntegration and TraceNestMiddleware.

Layout follows:
integrations/django/
  __init__.py      — re-export
  integration.py   — DjangoIntegration orchestration
  request.py       — SERVER request waterfall span
  middleware.py    — load_middleware wrapper
  middleware_init.py — TraceNestMiddleware for auto-init
  view.py          — view span
  template.py      — template span
"""
from .integration import DjangoIntegration
from .middleware_init import TraceNestMiddleware

__all__ = ["DjangoIntegration", "TraceNestMiddleware"]
