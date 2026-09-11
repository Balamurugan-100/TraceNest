"""Configuration handling for TraceNest SDK."""

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


def _str_to_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _detect_django_service_name() -> Optional[str]:
    """Try to auto-detect service name from Django settings."""
    try:
        from django.conf import settings
        # Check common Django settings for service/app name
        return (
            getattr(settings, "TRACENEST_SERVICE_NAME", None)
            or getattr(settings, "OTEL_SERVICE_NAME", None)
            or getattr(settings, "SERVICE_NAME", None)
        )
    except Exception:
        return None


def _detect_django_env() -> Optional[str]:
    """Try to auto-detect environment from Django settings."""
    try:
        from django.conf import settings
        return (
            getattr(settings, "TRACENEST_ENVIRONMENT", None)
            or getattr(settings, "ENVIRONMENT", None)
            or getattr(settings, "ENV", None)
        )
    except Exception:
        return None


def _parse_headers(headers_str: Optional[str]) -> Dict[str, str]:
    if not headers_str:
        return {}
    headers = {}
    for item in headers_str.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            headers[k.strip()] = v.strip()
    return headers


DEFAULT_EXCLUDE_PATTERNS = [
    "django/forms/*",
    "debug_toolbar/*",
    "*/widgets/*",
]


@dataclass
class SDKConfig:
    """Configuration options for TraceNest SDK."""

    service_name: str = "unknown-service"
    environment: str = "development"
    version: str = "0.1.0"
    endpoint: str = "http://localhost:4318"
    traces_endpoint: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    sample_rate: float = 1.0
    disabled: bool = False
    debug: bool = False
    resource_attributes: Dict[str, Any] = field(default_factory=dict)
    integrations: Dict[str, bool] = field(default_factory=dict)
    trace_nested_templates: bool = True
    template_enabled: bool = True
    template_exclude: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_PATTERNS))
    db_two_tier_spans: bool = False
    tags: Dict[str, Any] = field(default_factory=dict)
    on_request_span: Optional[Callable] = None

    @classmethod
    def from_env_and_kwargs(
        cls,
        service: Optional[str] = None,
        service_name: Optional[str] = None,
        environment: Optional[str] = None,
        version: Optional[str] = None,
        endpoint: Optional[str] = None,
        traces_endpoint: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        sample_rate: Optional[float] = None,
        disabled: Optional[bool] = None,
        debug: Optional[bool] = None,
        resource_attributes: Optional[Dict[str, Any]] = None,
        integrations: Optional[Dict[str, bool]] = None,
        **extra: Any,
    ) -> "SDKConfig":
        """Build SDKConfig by prioritizing explicit kwargs over environment variables."""

        # 1. Service name (auto-detect from Django settings if available)
        resolved_service = (
            service
            or service_name
            or os.getenv("TRACENEST_SERVICE_NAME")
            or os.getenv("TRACENEST_SERVICE")
            or os.getenv("OTEL_SERVICE_NAME")
            or os.getenv("TP_OBS_SERVICE_NAME")
            or _detect_django_service_name()
            or "unknown-service"
        )

        # 2. Environment (auto-detect from Django settings if available)
        resolved_env = (
            environment
            or os.getenv("TRACENEST_ENV")
            or os.getenv("TRACENEST_ENVIRONMENT")
            or os.getenv("OTEL_ENVIRONMENT")
            or os.getenv("DEPLOYMENT_ENVIRONMENT")
            or os.getenv("ENVIRONMENT")
            or os.getenv("ENV")
            or _detect_django_env()
            or "development"
        )

        # 3. Version
        resolved_version = (
            version
            or os.getenv("TRACENEST_VERSION")
            or os.getenv("OTEL_SERVICE_VERSION")
            or os.getenv("SERVICE_VERSION")
            or "0.1.0"
        )

        # 4. Endpoints & Headers
        resolved_endpoint = (
            endpoint
            or os.getenv("TRACENEST_ENDPOINT")
            or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
            or os.getenv("TP_OBS_ENDPOINT")
            or "http://localhost:4318"
        )

        resolved_traces_endpoint = (
            traces_endpoint
            or os.getenv("TRACENEST_TRACES_ENDPOINT")
            or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            or None
        )

        env_headers = _parse_headers(
            os.getenv("TRACENEST_HEADERS") or os.getenv("OTEL_EXPORTER_OTLP_HEADERS")
        )
        merged_headers = dict(env_headers)
        if headers:
            merged_headers.update(headers)

        # 5. Sample rate
        if sample_rate is not None:
            resolved_sample_rate = float(sample_rate)
        elif "TRACENEST_SAMPLE_RATE" in os.environ:
            try:
                resolved_sample_rate = float(os.environ["TRACENEST_SAMPLE_RATE"])
            except ValueError:
                resolved_sample_rate = 1.0
        elif "OTEL_TRACES_SAMPLER_ARG" in os.environ:
            try:
                resolved_sample_rate = float(os.environ["OTEL_TRACES_SAMPLER_ARG"])
            except ValueError:
                resolved_sample_rate = 1.0
        elif "SAMPLE_RATE" in os.environ:
            try:
                resolved_sample_rate = float(os.environ["SAMPLE_RATE"])
            except ValueError:
                resolved_sample_rate = 1.0
        else:
            resolved_sample_rate = cls.__dataclass_fields__["sample_rate"].default

        # Clamp sample rate between 0.0 and 1.0
        resolved_sample_rate = max(0.0, min(1.0, resolved_sample_rate))

        # 6. Disabled
        if disabled is not None:
            resolved_disabled = bool(disabled)
        else:
            resolved_disabled = _str_to_bool(
                os.getenv("TRACENEST_DISABLED")
                or os.getenv("OTEL_SDK_DISABLED")
                or os.getenv("TP_OBS_DISABLED"),
                default=False,
            )

        # 7. Debug
        if debug is not None:
            resolved_debug = bool(debug)
        else:
            resolved_debug = _str_to_bool(
                os.getenv("TRACENEST_DEBUG")
                or os.getenv("TP_OBS_DEBUG")
                or os.getenv("OTEL_LOG_LEVEL") == "debug",
                default=False,
            )

        # 8. Extra resource attributes & integrations
        res_attrs = dict(resource_attributes or {})
        integs = dict(integrations or {})

        # 9. Trace nested templates (default True matching Datadog APM behavior)
        if "trace_nested_templates" in extra and extra["trace_nested_templates"] is not None:
            resolved_trace_nested = _str_to_bool(extra["trace_nested_templates"], default=True)
        else:
            resolved_trace_nested = _str_to_bool(
                os.getenv("TRACENEST_DJANGO_TRACE_NESTED_TEMPLATES")
                or os.getenv("TP_OBS_DJANGO_TRACE_NESTED_TEMPLATES")
                or os.getenv("OTEL_PYTHON_DJANGO_TRACE_NESTED_TEMPLATES"),
                default=True,
            )

        # 10. Template instrumentation config (enabled + exclude patterns)
        default_exclude = list(DEFAULT_EXCLUDE_PATTERNS)
        tmpl_config = extra.get("template_instrumentation") or extra.get("TEMPLATE_INSTRUMENTATION")
        if isinstance(tmpl_config, dict):
            resolved_template_enabled = _str_to_bool(tmpl_config.get("enabled", True), default=True)
            resolved_template_exclude = tmpl_config.get("exclude", default_exclude)
        else:
            resolved_template_enabled = _str_to_bool(extra.get("template_enabled", True), default=True)
            if "template_exclude" in extra and extra["template_exclude"] is not None:
                resolved_template_exclude = extra["template_exclude"]
            elif "TRACENEST_TEMPLATE_EXCLUDE" in os.environ:
                resolved_template_exclude = [
                    p.strip() for p in os.environ["TRACENEST_TEMPLATE_EXCLUDE"].split(",") if p.strip()
                ]
            elif "TP_OBS_TEMPLATE_EXCLUDE" in os.environ:
                resolved_template_exclude = [
                    p.strip() for p in os.environ["TP_OBS_TEMPLATE_EXCLUDE"].split(",") if p.strip()
                ]
            else:
                resolved_template_exclude = default_exclude

        # 11. 2-Tier Database Spans (Datadog Parity: connection alias -> driver db name)
        resolved_db_two_tier = _str_to_bool(
            extra.get("db_two_tier_spans", os.getenv("TRACENEST_DB_TWO_TIER_SPANS", os.getenv("TRACENEST_DB_TWO_TIER", "false"))),
            default=False,
        )

        # 12. Static tags (applied to all spans)
        # Kwargs tags override env vars entirely (consistent with other settings)
        kwarg_tags = extra.get("tags")
        if kwarg_tags is not None:
            resolved_tags = dict(kwarg_tags)
        else:
            resolved_tags = {}
            env_tags_str = os.getenv("TRACENEST_TAGS") or os.getenv("TP_OBS_TAGS")
            if env_tags_str:
                for item in env_tags_str.split(","):
                    item = item.strip()
                    if "=" in item:
                        k, v = item.split("=", 1)
                        resolved_tags[k.strip()] = v.strip()

        # 13. Per-request span callback
        resolved_on_request_span = extra.get("on_request_span")

        return cls(
            service_name=resolved_service,
            environment=resolved_env,
            version=resolved_version,
            endpoint=resolved_endpoint,
            traces_endpoint=resolved_traces_endpoint,
            headers=merged_headers,
            sample_rate=resolved_sample_rate,
            disabled=resolved_disabled,
            debug=resolved_debug,
            resource_attributes=res_attrs,
            integrations=integs,
            trace_nested_templates=resolved_trace_nested,
            template_enabled=resolved_template_enabled,
            template_exclude=resolved_template_exclude,
            db_two_tier_spans=resolved_db_two_tier,
            tags=resolved_tags,
            on_request_span=resolved_on_request_span,
        )
