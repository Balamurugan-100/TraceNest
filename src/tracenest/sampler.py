"""Custom rule-based sampler for TraceNest SDK v3 supporting endpoint ignoring and custom per-route sampling rates."""

import fnmatch
from typing import Any, Dict, List, Optional, Sequence
from opentelemetry.context import Context
from opentelemetry.sdk.trace.sampling import (
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
    TraceIdRatioBased,
)
from opentelemetry.trace import Link, SpanKind, TraceFlags


class TraceNestRuleBasedSampler(Sampler):
    """
    Custom Sampler supporting:
    1. ignore_endpoints: Wildcard path patterns (e.g. ["/health*", "*/static/*"]) that return Decision.DROP.
    2. endpoint_sample_rules: Dictionary of path patterns to sample rates (e.g. {"/api/checkout/*": 1.0}).
    3. Global fallback ratio-based sampling.
    """

    def __init__(
        self,
        global_sample_rate: float = 1.0,
        ignore_endpoints: Optional[List[str]] = None,
        endpoint_sample_rules: Optional[Dict[str, float]] = None,
    ):
        self.global_sample_rate = max(0.0, min(1.0, float(global_sample_rate)))
        self.ignore_endpoints = list(ignore_endpoints or [])
        self.endpoint_sample_rules = dict(endpoint_sample_rules or {})

        # Pre-instantiate ratio samplers for fast lookup
        self._global_ratio_sampler = TraceIdRatioBased(self.global_sample_rate)
        self._rule_ratio_samplers: Dict[str, TraceIdRatioBased] = {
            pattern: TraceIdRatioBased(max(0.0, min(1.0, float(rate))))
            for pattern, rate in self.endpoint_sample_rules.items()
        }

    def should_sample(
        self,
        parent_context: Optional[Context],
        trace_id: int,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[Dict[str, Any]] = None,
        links: Optional[Sequence[Link]] = None,
    ) -> SamplingResult:
        attributes = attributes or {}

        # Extract target request path from common OpenTelemetry attribute conventions or span name
        target_path = (
            attributes.get("http.target")
            or attributes.get("url.path")
            or attributes.get("http.route")
            or name
        )
        if isinstance(target_path, str):
            # Clean query parameters if present in http.target (e.g., /health?verbose=1 -> /health)
            clean_path = target_path.split("?")[0]

            # 1. Check ignore patterns
            for pattern in self.ignore_endpoints:
                if fnmatch.fnmatch(clean_path, pattern):
                    return SamplingResult(Decision.DROP)

            # 2. Check custom endpoint sampling rules
            for pattern, ratio_sampler in self._rule_ratio_samplers.items():
                if fnmatch.fnmatch(clean_path, pattern):
                    return ratio_sampler.should_sample(
                        parent_context=parent_context,
                        trace_id=trace_id,
                        name=name,
                        kind=kind,
                        attributes=attributes,
                        links=links,
                    )

        # 3. Fallback to global sample rate
        return self._global_ratio_sampler.should_sample(
            parent_context=parent_context,
            trace_id=trace_id,
            name=name,
            kind=kind,
            attributes=attributes,
            links=links,
        )

    def get_description(self) -> str:
        return f"TraceNestRuleBasedSampler(global={self.global_sample_rate}, ignores={len(self.ignore_endpoints)}, rules={len(self.endpoint_sample_rules)})"


def create_tracenest_sampler(
    global_sample_rate: float = 1.0,
    ignore_endpoints: Optional[List[str]] = None,
    endpoint_sample_rules: Optional[Dict[str, float]] = None,
) -> Sampler:
    """Creates a ParentBased sampler wrapping TraceNestRuleBasedSampler."""
    root_sampler = TraceNestRuleBasedSampler(
        global_sample_rate=global_sample_rate,
        ignore_endpoints=ignore_endpoints,
        endpoint_sample_rules=endpoint_sample_rules,
    )
    return ParentBased(root=root_sampler)
