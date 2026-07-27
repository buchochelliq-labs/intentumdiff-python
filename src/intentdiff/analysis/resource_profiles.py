"""Guardrail resource-profile language set.

The resource-shaped matching + label enrichment that once lived here is
Rust-authoritative (#90 profile enrichment, #91 guardrail keying) — the keying
runs in the core (``resource_profile_key`` etc. in ``rust-core-host``). Only the
language set survives, consumed by ``guardrails._parse_rule`` to validate that a
protected rule targets a supported resource language. Everything else (the
``ResourceProfile`` dataclass and the Python keying/enrichment mirror) was
deleted once nothing consumed it (#91).
"""

from __future__ import annotations

RESOURCE_PROFILE_LANGUAGES = frozenset({"dockerfile", "hcl", "puppet"})
