# context-decision v1 owner protocol (한국어)

Bobbin 1.0.0의 설치·설정은 단일 `$bobbin:init`을 사용한다. 아래 사용자 승인 설명은 explicit 모드 기준이며, auto/adaptive는 [공통 기록 정책](../../context/references/recording-policy.md)에 따라 같은 검증 경로를 사용한다. 사용자 의사와 의미 검증은 승인 모드와 무관하게 유지한다. 기존 owner·schema 식별자는 내부 호환 계약이며 별도 설치 단위가 아니다.

`decision_cli.py`는 `context-decision/v1`의 semantic owner다. complete DEC draft, lifecycle effect, `context-owner-plan/v1`, `context-owner-validation-receipt/v1`과 bounded recall만 만든다. filesystem write, directory 생성, index 갱신, lock, final approval digest 생성과 apply는 하지 않는다. physical writer는 `context-core` coordinator 하나다.

## Dependency boundary

- marketplace: `bobbin`
- plugin: `bobbin`
- selector: `bobbin@bobbin`
- source: `Jeis-Jw/bobbin`
- protocol: `context-common/v2`

`schema`, `capabilities`, `check`, `search`, `read`, `brief`, `spec-view`, `conflicts`, `revisit`는 core 없이 호출할 수 있다. 저수준 write pipeline은 compatibility mode로 `--host`, `--core-inventory @file`, `--core-doctor @file`을 받는다. 일반 workflow와 init은 같은 Bobbin 패키지에 포함된 core의 entrypoint path·SHA-256 pin을 먼저 확인한 뒤 schema와 doctor를 직접 handshake한다. `doctor.repository_state=absent`는 bootstrap-required state이고 partial/invalid diagnostics는 전역 차단하지 않는다. decision owner는 install, enable, update, marketplace add, cache probing 또는 별도 runtime 복제를 수행하지 않는다.

## Semantic claim gate

DEC는 현재 또는 미래 행동을 지배하는 명시적 선택이며 다음 assertion 전부가 exact candidate에 결박돼야 한다.

- `explicit_choice` → `/owner_inputs/decision/decision`
- `scope_identified` → `/scope_hint`
- `commitment_present` → `/evidence/*`

idea, question, fact, preference와 미합의 제안은 `decline` 또는 `needs_clarification`이다. `requested_kind:"decision"`은 owner 선택만 고정하며 이 gate를 우회하지 않는다. CLI는 agent skill의 의미 판단을 대신하지 않고 assertion set, input digest와 RFC 6901 pointer만 fail-closed 검증한다.

저수준 direct surface는 2단계다. `candidate prepare`는 caller가 명시한 semantic field, caller가 제공한 `cand_`+32 lowercase hex transport ID, commitment evidence와 bounded search terms를 정규화한다. transport ID는 기계적으로 만들 수 있지만 의미상 가중치가 없다. owner는 caller 입력 없이 후보 의미를 지어내지 않는다. canonical workflow는 UUID4 transport ID만 자동 발급하고 `captured_from`을 `conversation`으로 기본화할 수 있으며 semantic field, evidence나 attestation은 발명하지 않는다. owner skill이 그 object를 판독한 뒤 `capture --candidate @file --attestation @file`로 claim하거나 `--decline-reason`/`--needs-clarification-reason`으로 권위 draft 없이 종료한다. 상수 commitment evidence와 CLI 자체 attestation은 금지다.

## DEC schema와 slot

canonical 필수 section은 `Decision`, `Rationale`, `Rejected alternatives`다. 세 section의 누락, 빈 값과 placeholder는 실패한다. 실제 검토한 대안이 없으면 `Not reviewed: <reason>`처럼 이유를 명시한다. 선택 canonical section은 `Evidence and constraints`, `Trade-offs`, `Revisit conditions`다. 한국어 `결정`, `취지`, `반려대안`, `근거와 제약`, `트레이드오프`, `재평가 조건`은 기존 artifact를 읽고 기존 heading 그대로 round-trip하기 위한 legacy alias일 뿐이며 신규 artifact에는 canonical 영어 heading을 쓴다. `verified_at`과 공통 `status`는 금지한다.

`scope`는 trim → NFKC+casefold → leading/trailing slash 제거 → segment별 non-alnum run을 `-`로 변환한다. empty segment, `.`/`..`, segment 40자 초과, 8 segment 초과와 전체 160자 초과는 실패한다. `decision_key`는 같은 변환을 사용하고 `/`, empty와 80자 초과를 거부한다. ancestor는 canonical segment 배열의 strict prefix이며 문자열 prefix나 equality가 아니다.

Current에는 `(scope, decision_key)`당 DEC가 최대 하나다. 같은 key의 ancestor/descendant scope는 overlap conflict이며 모든 conflict ID에 대한 acknowledgement와 `{id,path,sha256}` exact read precondition이 있어야 한다. 의미상 동일한 결정은 fingerprint로 판정하지 않는다. 사전 `check`가 제공한 실제 본문을 agent가 비교하고 `same`이면 기존 DEC를 재사용한다.

## Owner result와 lifecycle

capture는 one current draft/effect/create operation을 반환한다. ID와 `created_at`은 draft 시 한 번 만들고 embedded candidate, claim attestation, complete content와 semantic projection에 결박한다.

- `supersede`: successor candidate가 predecessor의 canonical scope/key를 명시적으로 그대로 가져야 한다. 한 owner result에 old changed-move History draft와 new Current create draft를 포함하고 `old.superseded_by == new.id`, `new.supersedes == [old.id]`를 지킨다. History path는 `<stem>--<old-id12>.md`다.
- `withdraw`: old를 `retired_reason:"withdrawn"`과 `retirement_note`가 있는 History draft로 만들며 successor가 없다.
- `annotate`: title, summary, tags, search terms, source refs만 제자리 correction하고 결정 section, slot과 ID는 보존한다.
- `revisit`: due warning과 review proposal만 반환하며 state를 바꾸지 않는다.

일반 evidence OBS는 active인 채 DEC `relations.informed_by`로 연결한다. decision-like fallback OBS import는 `kind_hint:decision`, source artifact의 exact id·path·SHA-256·actual claim, `same_claim` attestation과 cross-owner single coordinator plan을 요구하며 일반 evidence relation과 혼용하지 않는다.

## Same-batch validation

`batch validate`는 physical `decision.index.md`의 exact SHA-256를 base로 사용한다. 전달된 prior same-area final bundle을 proposal order대로 overlay한다. 각 bundle의 `plan.prior_bundle_digests`는 앞선 exact digest 목록과 같아야 한다. virtual Current에 slot, overlap acknowledgement/read precondition과 lifecycle predecessor current 여부를 적용한다.

성공 receipt는 다음을 결박한다.

- `owner_result_digest`
- `base_area_index_sha256`
- ordered `prior_same_area_bundle_digests`
- canonical `scope`, `decision_key`, actual `primary_claim`, `rationale`, acknowledged conflicts
- 자기 field를 제외한 `receipt_digest`

Receipt 없는 final owner plan이나 altered receipt는 `plan validate`에서 실패한다.

## Frozen workflow receipt

일반 capture는 `decision_workflow.py record --approved`이며 아래 frozen workflow를 내부에서 실행한다. low-level orchestration에서는 semantic approval 뒤 `decision_workflow.py preview --inline`과 receipt locator 없는 `apply`를 사용할 수 있다. caller는 decision semantic field와 `explicit_choice`, `scope_identified`, `commitment_present`를 각각 명시적으로 attest하고 candidate ID와 `captured_from:conversation`은 자동 기본값을 쓴다. agent는 frozen result가 semantic delta를 추가하지 않는지 확인하고 preview stdout의 `result.approval_digest`를 receipt 밖에 유지해 내부 `--approved-digest`로 그대로 전달한다. 저장 파일 본문을 보여주거나 별도 저장 질문을 하지 않으며 digest는 integrity binding일 뿐 사용자 승인 근거가 아니다. `preview --supersede <id>`와 `preview --withdraw <id> --reason <text>`도 같은 경로를 재사용한다. withdraw에는 semantic candidate가 없으므로 workflow가 private receipt 파일명/envelope에만 UUID4 transport ID를 발급하며 owner result와 approval preview에는 넣지 않는다. `preview`는 loaded core CLI의 인접 manifest와 compatible major를 확인하고 actual digest를 receipt에 결박한 뒤 schema와 current doctor를 직접 handshake하고 owner-result 생성, same-batch validation과 core transaction preview를 한 process에서 수행한다.

확정된 `context-decision-workflow-receipt/v1` envelope의 exact field는 `schema`, `status`, `created_at`, `candidate_id`, `operation`, `approval_material`, `approval_digest`, `receipt_digest`다. `status`는 변경하지 않는 `pending`, `operation`은 `capture|supersede|withdraw`, `created_at`은 timezone-aware RFC 3339다. approval material의 exact field는 `schema`, `vault_identity`, `core`, `operation`, `workflow_input_digest`, `owner_result_digest`, `core_approval_digest`, `core_bundle`다. digest는 exact `context-vault-identity/v1`, core absolute path/pinned SHA-256, operation/input/result digest, nested core approval digest와 complete bundle을 결박한다. `receipt_digest`는 손상 탐지용이고 receipt 내부 `approval_digest`도 독립적인 approval channel이 아니다. 모든 값을 재계산한 receipt도 write 전에 agent가 유지한 preview stdout digest와 같아야 한다.

canonical preview는 prospective default path가 vault 밖인지 mkdir 전에 검증하고 owner-only mode `0700` `tempfile.gettempdir()/context-decision` directory와 mode `0600` `<candidate_id>.json` regular file 한 개를 만든다. explicit `--receipt-file`이면 default directory를 만들거나 검사하지 않는다. 기존 default directory가 symlink·다른 mode·다른 owner 등 unsafe 상태면 migration이나 자동 수리 없이 실패한다. path traversal, symlink, non-regular file, repository-local receipt, 복사·이동·같은 경로에 재생성한 vault의 승인 재사용, core byte 변경, 잘못된 approval, precondition 변경과 CAS/lock 실패는 write 전에 막힌다.

자동 apply/reject 후보는 default filename·mode `0600` regular·valid v1이고 mtime 24시간 이내이며 현재 vault identity와 core path/SHA에 결박되고 frozen plan precondition이 아직 pending repository state를 가리키는 receipt뿐이다. 정확히 한 개일 때만 선택하고 0개·복수는 fail-closed이며 mtime/newest로 tie를 깨지 않는다. pending 여부는 frozen core plan에서 계산하고 receipt를 갱신하거나 재생성하지 않으므로 apply됐거나 stale인 kept receipt는 제외된다. canonical apply는 receipt locator만 생략하며 외부 preview digest는 required `--approved-digest`로 항상 전달한다. explicit `--receipt-file`도 같은 receipt·binding·digest·core apply 검증을 거친다.

apply 성공은 `--keep-receipt`가 없으면 receipt를 제거한다. cleanup 실패는 `applied:true`를 유지하고 `receipt_cleanup_failed` warning만 더하며 repository apply를 재시도하지 않는다. `reject`는 선택한 receipt만 제거하고 repository byte를 쓰지 않는다. preview 시작 sweep는 24시간을 넘긴 default-name·mode `0600` regular·valid v1 receipt만 제거하고 symlink, directory, unexpected filename, malformed JSON, 다른 schema는 건드리지 않는다. kept applied receipt는 byte-for-byte frozen 상태로 즉시 자동 선택에서 빠지고 같은 TTL 뒤 sweep 대상이 된다.

inline `--sec-*`는 plain literal을 기본으로 하고 explicit `@file`과 leading `@` literal용 `@@text`를 core body argument와 같은 의미로 해석한다. path-like plain text는 file로 추측하지 않으며 missing·symlink·8 KiB 초과 file은 receipt/repository write 전에 거부한다. common primary-claim protocol 상한은 2,000 codepoint이고 built-in SNAP `current_context`, OBS `observation`, DEC `decision`은 각각 owner-specific 1,200 codepoint다. canonical owner input 8 KiB와 candidate envelope 16 KiB를 적용하고 오류는 실제 크기·상한·초과량을 반환한다.

## Recall과 init

`search`는 `decision.index.md` metadata만 읽는다. `read`와 `brief`는 선택된 DEC만 연다. brief는 canonical `Decision`, `Rationale`, `Rejected alternatives`만 포함하고 최대 8 KiB다. legacy 한국어 heading도 읽지만 결과 key는 canonical 영어로 반환한다. 낮은 순위 item을 통째로 제외하며 section 중간 절단은 하지 않는다. History에는 항상 `do_not_follow:true`와 lifecycle reason을 붙인다.

`spec-view --scope <scope>`는 Stage 1의 Current metadata에서 canonical scope가 exact이거나 strict ancestor·descendant인 DEC만 선별한다. 문자열 prefix는 scope 관계가 아니다. 선택된 실제 본문의 canonical `Decision`·`Rationale`만 `(created_at,id)` 오름차순으로 반환한다. 기존 `결정`·`취지` heading은 legacy read alias로 받아 canonical 영어 key로 projection하며 저장된 heading을 자동 변경하지 않는다. History와 `do_not_follow`는 제외한다. JSON envelope와 마지막 newline을 포함한 실제 CLI stdout UTF-8는 최대 32 KiB이며 상한을 넘으면 같은 deterministic 순서의 뒤쪽 DEC를 항목 전체로 생략하고 exact `omitted_count`를 반환한다. section 중간 절단, approval, 저장과 write는 없고 매 호출 index와 실제 본문에서 재생성한다.

`check`는 `--scope`와 `--decision-key`를 둘 다 주거나 둘 다 생략한다. 하나만 주면 `usage_invalid`다. 좌표를 알면 exact check를 한 번만 실행한다. exact pair는 `coverage:exact_slot`이고 exact slot과 scope overlap을 반드시 포함한다. 둘 다 생략하면 lexical-only `coverage:discovery_only`와 exact caveat `no-conflict cannot be concluded; re-run with exact scope/decision_key before preview`를 반환한다. 그 밖의 후보는 statement·rationale·query와 title·summary·search terms의 distinctive metadata match만 선택하며 score 0의 임의 body는 열지 않는다. comparison input은 24 KiB, 전체 result는 32 KiB다. 선택 item은 실제 `Decision`, `Rationale`, `Rejected alternatives`와 비어 있지 않은 `Revisit conditions`를 `sections` 아래 반환하며 top-level revisit 복제는 없다. 같은 turn에서는 이 section을 다시 읽지 않고 재사용한다. agent는 `new|same|supporting|rationale_changed|conflict` 중 하나와 근거·관련 ID를 제시하되 discovery-only에서 무충돌을 확정하지 않는다. 이 operation은 read-only이고 지문·문장 유사도로 의미를 확정하지 않는다.

`rationale_changed|conflict`는 primary 결론 전에 반환된 비어 있지 않은 실제 Decision, Rationale, Rejected alternatives, Revisit conditions section을 모두 원문으로 인용한다. 영향받는 행동을 보류하고 keep이면 수행하지 않고 supersede면 그 명시적 선택 뒤에만 진행하는 두 선택지를 모두 제시해 하나의 명시적 양자 질문을 한다. 선택한 Revisit token을 `satisfied|no evidence|ambiguous` 중 하나로 user response에 그대로 쓰며 근거를 발명하지 않는다. `satisfied`는 사용자가 저장된 조건을 직접 성립시키는 현재 사실을 제공한 경우에만 쓰며 요청된 충돌 행동 자체는 근거가 아니다. 사실이 없거나 저장 조건이 아닌 다른 쟁점에 관한 사실이면 `no evidence`다. 저장 조건에 관한 사용자 사실이 관련은 있지만 불완전하거나 서로 충돌할 때만 `ambiguous`다. 조건 충족은 재평가 권한이지 구현 권한이 아니다. 명시적 선택은 해당 decision payload를 확정하고 별도 저장 질문 없이 capture를 승인한다.

`init`은 같은-major이며 `context-owner-descriptor/v2` feature를 지원하는 core에서 인접 manifest를 확인하고 actual digest를 operation 동안 고정한 뒤 schema와 doctor state를 직접 handshake한다. 이어 DEC area의 exact legacy-compatible `context-owner-descriptor/v1`, complete empty decision index seed, descriptor/seed digest와 installed core `bootstrap` 요청을 만든다. ready/partial/invalid과 bootstrap-required `absent`를 전달하되 실제 복구 가능 여부는 core가 판정한다. Init skill은 `decision_init.py` entrypoint 한 번으로 handshake와 active installed core의 public `context_cli.py bootstrap --host <host>` 호출을 순서대로 수행한다. core surface가 필요한 root 복구, decision area registration과 host별 managed operating policy installation을 coordinator로 적용하며 phase result를 반환한다. decision CLI 자체는 root/area/index/policy를 만들거나 수정하지 않는다.

`claim_fingerprint`, `source_claim_fingerprint`와 capture candidate의 `claim_key`는 schema에서 제거됐다. candidate ID는 transport reference로만 사용한다. legacy artifact field는 읽고 다음 승인 rewrite에서 제거하며, 신규 candidate/draft의 제거된 field는 `schema_removed_field`로 실패한다.

## Output and errors

JSON success는 `{"ok":true,"result":...}`, error는 `{"ok":false,"error":{"code":...,"message":...,"details":...}}`다. exit code는 usage/schema 2, not found 3, conflict 5, integrity/index 6이다. 모든 operation은 실행 전후 repository filesystem bytes가 같아야 한다.

`--search-term`을 생략한 direct candidate는 Decision, Rejected alternatives, Rationale, Revisit conditions에서 metadata term을 최대 12개 파생한다. title·summary의 term은 이미 index에 있으므로 파생 목록에서 제외한다. discovery는 결정적인 stem으로 distinctive hit를 비교·정렬하며 `frequency_cutoff`의 잡음 제거 의미를 유지한다. 저장 본문과 명시적 search term이 입력 정본이며 검색 점수로 의미 충돌을 판정하지 않는다.

Discovery는 기존 frequency cutoff(Current의 1/4을 올림한 상한)로 corpus 공통 잡음을 제거한다. 나머지 hit에는 IDF와 title·decision_key 가중치를 적용하고 preserve/preserving, boundary/boundaries 같은 어형도 정규화한다. 첫 결과는 lexical 점수가 가장 높다. 이후 후보는 이미 선택한 metadata와의 중복을 감점해 비슷한 주제의 반복 문서가 본문 읽기 예산을 독점하지 않게 한다. 이 metadata-only 보정은 exact-slot·scope-overlap coverage에는 적용하지 않고 저장 byte나 의미 동일성·conflict 판정을 바꾸지 않는다. 같은 범위를 지배하는 동일 선택이면 반환된 scope·decision_key를 재사용하고 유사어 key를 만들지 않는다.

## One-call record

`decision_workflow.py record`는 같은 `preview`와 같은 `apply`를 변경 없는 frozen receipt에 대해 한 프로세스 안에서 실행한다. caller는 사용자의 직접적·명시적·무조건적 semantic approval 뒤에만 `--approved`를 준다. `approval_digest`, 결박된 core SHA, vault identity, CAS, lock, atomic write는 2단계 경로와 동일하게 내부에서 결박되며 성공 시 receipt는 `--keep-receipt`가 없으면 제거된다. `--supersede`와 `--withdraw`는 `record`에서도 동일하다. `preview`, `apply`, `record`의 `--core-cli`는 선택이다. 같은 major의 manifest 검증된 sibling `context-core`가 정확히 하나일 때만 결정적으로 해석하고, 0개 또는 여러 개면 `core_cli_required`와 후보 목록으로 fail closed한다. 2단계 `preview`·`apply`는 orchestration용으로 유지된다.
