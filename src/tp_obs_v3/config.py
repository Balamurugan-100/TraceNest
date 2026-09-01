"""Configuration handling for tp_obs_v3 SDK."""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


def _str_to_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _parse_headers(headers_str: Optional[str]) -> Dict[str, str]:
    if not headers_str:
        return {}
    headers = {}
    for item in headers_str.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            headers[k.strip()] = v.strip()
    return headers


@dataclass
class SDKConfig:
    """Configuration options for tp_obs_v3 SDK."""

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

        # 1. Service name
        resolved_service = (
            service
            or service_name
            or os.getenv("OTEL_SERVICE_NAME")
            or os.getenv("TP_OBS_SERVICE_NAME")
            or "unknown-service"
        )

        # 2. Environment
        resolved_env = (
            environment
            or os.getenv("OTEL_ENVIRONMENT")
            or os.getenv("DEPLOYMENT_ENVIRONMENT")
            or os.getenv("ENVIRONMENT")
            or os.getenv("ENV")
            or "development"
        )

        # 3. Version
        resolved_version = (
            version
            or os.getenv("OTEL_SERVICE_VERSION")
            or os.getenv("SERVICE_VERSION")
            or "0.1.0"
        )

        # 4. Endpoints & Headers
        resolved_endpoint = (
            endpoint
            or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
            or os.getenv("TP_OBS_ENDPOINT")
            or "http://localhost:4318"
        )

        resolved_traces_endpoint = (
            traces_endpoint
            or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            or None
        )

        env_headers = _parse_headers(os.getenv("OTEL_EXPORTER_OTLP_HEADERS"))
        merged_headers = dict(env_headers)
        if headers:
            merged_headers.update(headers)

        # 5. Sample rate
        if sample_rate is not None:
            resolved_sample_rate = float(sample_rate)
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
            resolved_sample_rate = 1.0

        # Clamp sample rate between 0.0 and 1.0
        resolved_sample_rate = max(0.0, min(1.0, resolved_sample_rate))

        # 6. Disabled
        if disabled is not None:
            resolved_disabled = bool(disabled)
        else:
            resolved_disabled = _str_to_bool(
                os.getenv("OTEL_SDK_DISABLED") or os.getenv("TP_OBS_DISABLED"),
                default=False,
            )

        # 7. Debug
        if debug is not None:
            resolved_debug = bool(debug)
        else:
            resolved_debug = _str_to_bool(
                os.getenv("TP_OBS_DEBUG") or os.getenv("OTEL_LOG_LEVEL") == "debug",
                default=False,
            )

        # 8. Extra resource attributes & integrations
        res_attrs = dict(resource_attributes or {})
        integs = dict(integrations or {})

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
        )
