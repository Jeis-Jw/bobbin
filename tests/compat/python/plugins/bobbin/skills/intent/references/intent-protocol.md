# Intent protocol

Bobbin 1.0.0 uses one package and one `$bobbin:init` entrypoint. User-approval instructions below describe explicit mode; auto/adaptive use the same validated path under the [shared recording policy](../../context/references/recording-policy.md). Semantic validity and genuine user commitment remain required independently of recording authorization. Owner and schema identifiers are compatibility contracts, not separate installation units.

`context-intent/v1` uses descriptor v2 with authoritative slot `(scope, intent_key)`. `Intent` is required; `Success criteria`, `Constraints`, and `Revisit conditions` are optional.

The claim attestation binds `intent_present` and `desired_direction` to the exact candidate. Supersede additionally binds `same_semantic_claim` to the predecessor and successor's actual primary bodies. The owner re-derives the complete result before issuing `context-owner-validation-receipt/v2`.

Only `context-core` writes a vault. The owner has no filesystem write primitive and never installs another plugin. A DEC may independently use `serves:intent`; INTENT does not store an inverse edge.
