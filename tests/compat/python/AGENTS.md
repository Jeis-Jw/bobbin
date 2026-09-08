# Bobbin 작업정책

- 이 저장소는 `context-manager`의 독립 plugin component인 Bobbin을 소유한다. `context-core`, `context-decision`, `context-assumption`, `context-term`, `context-intent`, `context-document`는 단일 `plugins/bobbin` 패키지의 내부 semantic owner/runtime 식별자다.
- public product/runtime contract는 root README, plugin README, `skills/**/references/*.md`, executable schema와 tests가 소유한다.
- 이 저장소는 public component 예외를 적용한다. 내부 개발 intent·decision·observation·handoff는 상위 `context-manager` vault에서 `context-plugins` scope로 관리하며 이 공개 repository에 `context/`를 만들거나 commit하지 않는다. plugin 사용자가 자기 프로젝트에 만드는 consumer vault는 이 경계와 무관하다.
- 이 저장소에는 `wiki/`를 만들지 않는다. durable project context는 선택된 filesystem vault root의 `context/`에 저장한다. Git은 공유와 버전 관리를 위한 선택 사항이며 context runtime의 전제가 아니다.
- `context-core`는 storage, index, routing, approval과 physical write를 소유하고 semantic owner는 자기 artifact의 의미, comparison과 lifecycle을 소유한다.
- 의미 동일성·conflict·rationale change는 hash, ID나 index metadata가 아니라 실제 body, scope와 rationale로 판단한다.
- 소비자 프로젝트의 durable write는 `.bobbin/config.json`의 `explicit|auto|adaptive` 승인 모드와 활성 기능을 따른다. 이 저장소의 내부 개발 context는 상위 vault 정책을 따르며 구현 승인을 context 기록 승인으로 확장하지 않는다. 모든 모드에서 frozen bundle, semantic validity와 physical write 검증을 유지한다.
- 모든 semantic owner는 같은 Bobbin 패키지의 core를 사용한다. 별도 plugin dependency를 설치하지 않으며 공개 버전은 `1.0.0`, protocol은 기존 `context-common/v2`다. 역사적 artifact schema와 scope는 리브랜딩하지 않는다.
- source, marketplace, version 또는 protocol 변경은 두 host catalogs, plugin manifests, fixtures, public docs와 distribution tests를 함께 갱신한다.
- 외부 remote 생성, push, marketplace publication과 license 선택은 별도 명시 승인 없이는 수행하지 않는다.

<!-- BEGIN context-core-policy (managed by context-core) -->
## Durable context workflow

- Resolve the active language from an explicit user language choice, then the host's preferred response language, then the established conversation language, and finally English. OS locale is not authoritative. Keep machine-readable surfaces in canonical English and preserve artifact prose.
- Bobbin is one plugin. On a durable signal, read project-local `.bobbin/config.json` for enabled features and approval mode. Missing config preserves legacy registered features and explicit mode. Shared vaults do not share settings. Only a direct user request through Bobbin init may change settings.
- Audit each user turn's new meaning once. No durable signal means zero context tool calls and no audit status or capture question. For a mechanical local edit, skip AGENTS/guidance discovery and exclude `context/`: use the named path, or infer one task subtree and search once. Never use `.`, `--hidden`, repository-wide globs or the repository root; ask for the path instead of widening. Keep the scope/anchor, read Current IDs and pending/dismissed references in a session-only ledger, never a copied vault.
- Recall metadata first only if prior context can change the answer, then read selected actual bodies. Let the semantic owner compare actual bodies, scope, and rationale. IDs and metadata are not semantic evidence. Report a conflict before the primary conclusion. Hold an action whose change intent is unresolved: keep means not performed; supersede requires an explicit user choice. A satisfied revisit condition permits reassessment, not implementation. Never ask again for an already explicit choice.
- Only enabled owners participate in automatic recall, routing and recording. Disabled features preserve records and explicit historical reads. SNAP is staging, OBS is evidence and DEC is an authoritative user choice. Do not turn uncertainty or an LLM preference into a user commitment.
- Finish the primary request first. Follow the loaded shared recording policy: `explicit` records on direct, explicit, unconditional semantic approval; `auto` records eligible durable context without per-record questions; `adaptive` lets the LLM choose record or ask based on meaning, scope, evidence, conflicts and consequences. Ask only when the selected mode requires it, never a second storage question.
- Core is the sole writer. Every mode uses internal preview and unchanged apply in the same response, with frozen payload, project/vault identity, CAS, lock and atomic-write checks. Never show the rendered storage body merely to authorize persistence. Never regenerate after approval; re-evaluate if settings or content change. A generic acknowledgement, praise, condition, edit request, or topic change is not user approval. Report policy-authorized records honestly. Recording policy grants no unrelated execution or publication permission.
<!-- END context-core-policy (managed by context-core) -->
