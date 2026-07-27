"""Guardrail keyed-data language set.

The schema-shaped keyed matching + label enrichment that once lived here is
Rust-authoritative (#90 profile enrichment, #91 guardrail keying) — the keying
runs in the core (``keyed_data_key`` etc. in ``rust-core-host``). Only the
language set survives, consumed by ``guardrails._parse_rule`` to validate that a
protected rule targets a supported keyed-data language. Everything else (the
``KeyedDataProfile`` dataclass, the per-language profile tables, and the Python
keying/enrichment mirror) was deleted once nothing consumed it (#91).
"""

from __future__ import annotations

KEYED_DATA_LANGUAGES = frozenset(
    {
        "adf",
        "databricks",
        "databricks-workflow",
        "dbt-config",
        "dbt-packages",
        "dbt-yaml",
        "json",
        "yaml",
    }
)
