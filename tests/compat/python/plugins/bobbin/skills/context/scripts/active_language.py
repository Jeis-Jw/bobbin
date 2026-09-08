#!/usr/bin/env python3
"""Portable signal precedence for the Bobbin active-language contract."""
from __future__ import annotations


NON_SWITCHING_SIGNALS = frozenset({
    "code",
    "filename",
    "identifier",
    "quotation",
    "isolated_foreign_term",
    "os_locale",
})
LANGUAGE_SIGNALS = frozenset({"current_request", "explicit_pin", "host_preference", "conversation_language"})


def _language(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().replace("_", "-").casefold()
    if not normalized or normalized == "auto":
        return None
    return normalized


def resolve_active_language(
    *,
    current_request: str | None = None,
    explicit_pin: str | None = None,
    host_preference: str | None = None,
    conversation_language: str | None = None,
) -> str:
    """Resolve provided semantic signals; detection remains the host/agent's job.

    A current-response request and a persistent pin are both explicit user choices.
    The current response wins only when those two explicit choices conflict.
    OS locale is intentionally absent from the resolver.
    """

    return next(
        value
        for value in (
            _language(current_request),
            _language(explicit_pin),
            _language(host_preference),
            _language(conversation_language),
            "en",
        )
        if value is not None
    )


def signal_may_switch_language(signal_kind: str) -> bool:
    """Only a semantic language-choice signal may switch an established language."""

    return signal_kind in LANGUAGE_SIGNALS


def qualifies_as_capture_approval(
    *,
    semantic_payload_confirmed: bool,
    scope_identified: bool,
    lifecycle_effects_identified: bool,
    direct: bool,
    explicit: bool,
    unconditional: bool,
    semantic_delta: bool = False,
    generic_acknowledgement: bool = False,
    praise_only: bool = False,
    edit_request: bool = False,
    topic_change: bool = False,
) -> bool:
    """Apply the language-independent semantic approval gate.

    Approval may be established naturally in conversation; it does not require a
    separate question over the rendered storage document. The frozen
    `approval_digest` remains an internal integrity binding enforced by core.
    """

    return (
        semantic_payload_confirmed
        and scope_identified
        and lifecycle_effects_identified
        and direct
        and explicit
        and unconditional
        and not semantic_delta
        and not generic_acknowledgement
        and not praise_only
        and not edit_request
        and not topic_change
    )
