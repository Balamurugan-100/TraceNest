"""View span — wraps BaseHandler._get_response, View.dispatch, and resolves view name/route."""

import functools
import inspect
import re
from typing import Any, Callable, Optional

from opentelemetry.trace import SpanKind, get_current_span

from tracenest.config import SDKConfig
from tracenest.tracing import traced_span
import tracenest

from .request import _normalize_route, _resolve_view_name

_config: Optional[SDKConfig] = None


def set_config(config: Optional[SDKConfig]) -> None:
    global _config
    _config = config


def _get_config() -> Optional[SDKConfig]:
    return _config if _config is not None else tracenest.get_config()


def _apply_tags(span):
    cfg = _get_config()
    if cfg and cfg.tags:
        for k, v in cfg.tags.items():
            span.set_attribute(k, v)


def _bind_response_parent(response: Any) -> None:
    if response is not None and hasattr(response, "render") and not getattr(response, "is_rendered", False):
        if not hasattr(response, "_tp_parent_span"):
            curr = get_current_span()
            if curr and curr.is_recording():
                response._tp_parent_span = curr


@functools.lru_cache(maxsize=512)
def _to_snake_case(name: str) -> str:
    """Convert CamelCase view class name to snake_case (memoized for high throughput)."""
    s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def traced_get_response(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    request = args[0] if args else kwargs.get("request")
    if request is None:
        return wrapped(*args, **kwargs)

    initial_route = getattr(request, "path", "/")

    with traced_span(
        "django.view",
        kind=SpanKind.INTERNAL,
        attributes={"span.type": "web", "component": "django", "django.view": "view", "http.route": initial_route},
        tracer_name="tracenest.django",
    ) as span:
        _apply_tags(span)
        response = wrapped(*args, **kwargs)
        method = getattr(request, "method", "GET")
        raw_view_name = _resolve_view_name(request, method)
        norm_route = _normalize_route(request, request.path if hasattr(request, "path") else initial_route)

        span.update_name(f"🐍 django.view.{raw_view_name}")
        span.set_attribute("django.view", raw_view_name)
        span.set_attribute("django.view.name", raw_view_name)
        span.set_attribute("resource.name", raw_view_name)
        span.set_attribute("http.route", norm_route)
        span.set_attribute("http.request.method", method.upper())
        _bind_response_parent(response)
        return response


def traced_view_setup(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    span_name = "⚙️ django.views.generic.base.View.setup"
    attrs = {
        "span.type": "web",
        "component": "django",
        "django.view.method": "setup",
        "resource.name": span_name,
    }
    with traced_span(span_name, kind=SpanKind.INTERNAL, attributes=attrs, tracer_name="tracenest.django"):
        res = wrapped(*args, **kwargs)
        return res


def traced_view_dispatch(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    view_cls = instance.__class__.__name__
    view_module = instance.__class__.__module__
    full_view_name = f"{view_module}.{view_cls}" if view_module else view_cls

    request = args[0] if args else kwargs.get("request")
    method = getattr(request, "method", "GET").lower() if request else "get"

    # Detect dispatch owner (e.g. rest_framework.views.APIView or View)
    dispatch_cls = full_view_name
    if hasattr(wrapped, "__qualname__") and "." in wrapped.__qualname__:
        owner_name = wrapped.__qualname__.rsplit(".", 1)[0]
        owner_mod = getattr(wrapped, "__module__", "")
        if owner_mod and owner_mod != "builtins":
            dispatch_cls = f"{owner_mod}.{owner_name}"

    dispatch_span_name = f"🐍 {dispatch_cls}.dispatch"
    dispatch_attrs = {
        "span.type": "web",
        "component": "django",
        "django.view.class": full_view_name,
        "django.view.name": view_cls,
        "django.view.method": "dispatch",
        "resource.name": dispatch_span_name,
    }

    with traced_span(dispatch_span_name, kind=SpanKind.INTERNAL, attributes=dispatch_attrs, tracer_name="tracenest.django") as dispatch_span:
        _apply_tags(dispatch_span)
        action = getattr(instance, "action", None)
        if action:
            dispatch_span.set_attribute("django.view.action", str(action))
        handler_method = action if action and hasattr(instance, action) else method
        handler = getattr(instance, handler_method, None)
        if handler and callable(handler) and not getattr(handler, "_tp_traced", False):
            # Datadog formats class handler as <module>.<view_snake_case>.<method>
            snake_cls = _to_snake_case(view_cls)
            handler_span_name = f"🐍 {view_module}.{snake_cls}.{handler_method}" if view_module else f"🐍 {snake_cls}.{handler_method}"
            handler_attrs = {
                "span.type": "web",
                "component": "django",
                "django.view.class": full_view_name,
                "django.view.name": view_cls,
                "django.view.method": handler_method,
                "resource.name": handler_span_name,
            }
            if action:
                handler_attrs["django.view.action"] = str(action)

            # Async handlers (async def get/post) return a coroutine that Django
            # awaits after dispatch returns. Guard with an async wrapper so the
            # handler span covers the actual execution instead of closing at ~0ms.
            if inspect.iscoroutinefunction(handler):
                async def _traced_handler(*h_args, **h_kwargs):
                    with traced_span(handler_span_name, kind=SpanKind.INTERNAL, attributes=handler_attrs, tracer_name="tracenest.django") as h_span:
                        h_res = await handler(*h_args, **h_kwargs)
                        _bind_response_parent(h_res)
                        return h_res
            else:
                def _traced_handler(*h_args, **h_kwargs):
                    with traced_span(handler_span_name, kind=SpanKind.INTERNAL, attributes=handler_attrs, tracer_name="tracenest.django") as h_span:
                        h_res = handler(*h_args, **h_kwargs)
                        _bind_response_parent(h_res)
                        return h_res

            _traced_handler._tp_traced = True
            orig_action_handler = getattr(instance, handler_method, None)
            orig_method_handler = getattr(instance, method, None) if method != handler_method else None

            setattr(instance, handler_method, _traced_handler)
            if orig_method_handler is not None:
                setattr(instance, method, _traced_handler)
            try:
                res = wrapped(*args, **kwargs)
                _bind_response_parent(res)
                return res
            finally:
                if orig_action_handler is not None:
                    setattr(instance, handler_method, orig_action_handler)
                if orig_method_handler is not None:
                    setattr(instance, method, orig_method_handler)
        else:
            res = wrapped(*args, **kwargs)
            _bind_response_parent(res)
            return res
