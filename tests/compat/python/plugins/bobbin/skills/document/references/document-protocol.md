# Document protocol

Bobbin 1.0.0 uses one package and one `$bobbin:init` entrypoint. User-approval instructions below describe explicit mode; auto/adaptive use the same validated path under the [shared recording policy](../../context/references/recording-policy.md). Semantic validity and genuine user commitment remain required independently of recording authorization. Owner and schema identifiers are compatibility contracts, not separate installation units.

`context-document/v1` uses descriptor v2 with authoritative slot `(scope, document_key)` and one required `Content` H2 section.

DOCUMENT is a current-state statement consumed by an agent or person through recall/envelopes. An external deliverable document remains repository-owned and is outside this owner. Each slot is a default-read budget: expand larger knowledge by stable chapter slots such as `design-skeleton`, `design-envelope`, and `design-rules`, not by enlarging one slot.

Capture binds `content_present` and `living_document` to the exact candidate. Update binds a live ID/path/SHA precondition and exact replacement Content, then emits one `replace` operation with generic topology `replace_same_state`. ID and slot remain stable.

Only `context-core` writes a vault. The owner has no filesystem write primitive and never installs another plugin. A DEC may independently use `affects:document`; DOCUMENT does not store an inverse edge.

Core refresh compares each Current DOCUMENT's `updated_at` or initial `created_at` with the `created_at` of Current DEC artifacts that target it through `affects:document`. A newer DEC produces the non-blocking `document-stale-vs-decision` hygiene warning until a later DOCUMENT update.
