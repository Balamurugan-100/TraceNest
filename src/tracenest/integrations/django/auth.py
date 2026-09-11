"""Auth spans — wraps django.contrib.auth login/authenticate operations."""
import logging
from typing import Any, Callable

from opentelemetry.trace import SpanKind

from tracenest.tracing import traced_span

logger = logging.getLogger("tracenest.integrations.django.auth")


def traced_login(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    user = args[1] if len(args) > 1 else kwargs.get("user")
    user_id = str(getattr(user, "pk", getattr(user, "id", ""))) if user else ""

    span_attrs = {"django.auth.action": "login"}
    if user_id:
        span_attrs["usr.id"] = user_id
        span_attrs["enduser.id"] = user_id

    with traced_span("🔐 django.auth.login", kind=SpanKind.INTERNAL, attributes=span_attrs, tracer_name="tracenest.django"):
        res = wrapped(*args, **kwargs)
        return res


def traced_authenticate(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    with traced_span(
        "🔐 django.auth.authenticate",
        kind=SpanKind.INTERNAL,
        attributes={"django.auth.action": "authenticate"},
        tracer_name="tracenest.django",
    ) as span:
        res = wrapped(*args, **kwargs)
        if res:
            user_id = str(getattr(res, "pk", getattr(res, "id", "")))
            if user_id:
                span.set_attribute("usr.id", user_id)
                span.set_attribute("enduser.id", user_id)
                span.set_attribute("auth.success", True)
        else:
            span.set_attribute("auth.success", False)
        return res
