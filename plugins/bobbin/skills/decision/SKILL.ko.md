---
name: decision
description: 선택 신호가 있을 때 Current DEC 실제 본문을 비교하고 명시적으로 승인된 선택만 준비한다.
---

실행 요건: Node.js 20.20.0 이상. 이 패키지의 `.mjs` 진입점을 사용하며 Python이나 전역 플러그인을 탐색하지 않는다.

# Decision

먼저 [공통 기록 정책](../context/references/recording-policy.md)을 따른다. 기능·승인은 `.bobbin/config.json`으로 정하며 아래 사용자 승인 절차는 explicit 모드에 적용한다. auto/adaptive는 같은 검증 경로에 정책 승인을 전달한다.

같은 Bobbin 패키지의 core만 사용한다. 대화 재-audit·직접 쓰기는 금지하며 core만 쓴다. `references/`, manifest, `context/*.index.md`는 읽지 않는다. `search`는 메타데이터, `compare`는 선택된 비교 본문을 읽는다. `--help`·plugin script read·grep은 금지다. 아래 완전한 명령을 사용하고 오류가 수정 방법을 알려주면 이를 반영한 뒤 재시도한다. 필요한 실제 비교가 뒷받침하지 않는 attestation은 추가하지 않는다. 설명되지 않는 interface failure 뒤에만 script source를 읽는다.

## 조회와 판정

1. core의 사용자 선택 신호에만 동작한다. 호환 요청 수행 뒤에는 비교를 추가하지 않는다. 같은 scope·anchor의 완전한 실제 본문과 유효한 비교 결과가 context에 있고 관련 mutation이 없으면 재사용하며, 재사용을 증명하려고 호출하지 않는다. scope/key를 알면 `decision_cli.mjs compare`를 한 번 실행한다. 모르면 메타데이터 전용 `search --query '<구별되는 검색어>'`를 사용하고 scope를 알면 `--scope '<scope>'`를 붙인다. 메타데이터로 좌표를 고른 뒤 실제 본문을 비교해야 하며 기존 선택에 유사어 key를 만들지 않는다. 검색 결과가 비었다고 무충돌을 확정하거나 전역 검색을 자동 반복하지 않는다. scope가 없으면 확인하고, 실제 신규 선택은 caller가 제공한 새 key로 exact 비교한다.
2. 같은 턴에서는 위 조건을 충족하는 `compare`의 section을 재사용한다. 필요한 실제 내용이 없거나 관련 mutation을 아는 경우가 아니면 `read`, `spec-view` 또는 다른 context read를 다시 호출하지 않는다. 실제 `Decision`, `Rationale`, `Rejected alternatives`와 비어 있지 않은 `Revisit conditions`를 비교해 `new|same|supporting|rationale_changed|conflict`로 판정한다. 유사도·hash·ID·metadata는 의미 근거가 아니다.
3. `same`은 조용히 재사용하고 `supporting`은 DEC를 유지하며 오래 갈 새 근거만 OBS 후보로 본다. `rationale_changed|conflict`는 primary 결론 전에 반환된 비어 있지 않은 실제 Decision, Rationale, Rejected alternatives, Revisit conditions를 모두 원문 인용한다. 선택한 revisit token을 `satisfied|no evidence|ambiguous` 중 하나로 user response에 그대로 쓰며 근거를 발명하지 않는다. `satisfied`는 사용자가 저장 조건을 직접 성립시키는 현재 사실을 제공할 때만 쓴다. 요청된 충돌 행동 자체는 근거가 아니다. 사실이 없거나 저장 조건이 아닌 다른 쟁점에 관한 사실이면 `no evidence`, 관련 조건 사실이 불완전하거나 충돌하면 `ambiguous`다.
4. 사용자의 답까지 영향받는 행동을 보류하고 이를 수행·진행하는 code·file·command 변경을 하지 않는다. 두 선택지를 모두 제시해 하나의 명시적 양자 질문을 한다. keep이면 수행하지 않고 supersede면 그 명시적 선택 뒤에만 진행한다. 조건 충족은 재평가 권한이지 구현 권한이 아니다. 명시적 선택이 해당 decision payload를 확정하고 별도 저장 질문 없이 capture를 승인한다. `new`는 반환 범위뿐이다.
5. 현재·미래 행동을 지배하는 명시적 선택, canonical scope, commitment evidence가 모두 caller에게서 왔을 때만 claim한다. 원래 요청을 먼저 끝내고 성숙한 후보를 한 번 묶어 제안한다. 새 근거 없이 dismissed/deferred 후보를 재제안하지 않는다. payload·scope·lifecycle effect가 미확정이면 그 semantic delta만 묻는다.

신규 canonical section은 `Decision`, `Rationale`, `Rejected alternatives`이며 기존 한국어 heading은 legacy read/round-trip alias다.

같은 범위를 지배하는 동일 선택이면 반환된 `scope`·`decision_key`를 재사용하고 유사어 key를 만들지 않는다.

`compare`는 선택된 각 Current의 실제 `path`와 완전한 비교 section을 `comparison.current`에 반환하고, `current_links`에는 같은 선택 ID의 `state:"current"`와 `supersedes` predecessor ID를 추가한다. 이 필드는 기록을 찾아가는 정보이며 의미 동일성의 근거가 아니다. predecessor를 찾기 위해 Current를 다시 읽지 않는다. 과거 기록이 필요하고 실제 본문이 context에 없을 때만 predecessor를 stable ID로 읽는다. supersede는 파일 경로를 옮기므로 추측한 파일명이나 예전 Current 경로 대신 마지막으로 반환된 실제 `path`를 사용한다. History는 `state:"history"`, `do_not_follow:true`, lifecycle reason을 반환하며 현재 선택으로 따르지 않는다.

```bash
node /loaded/bobbin/skills/decision/scripts/decision_cli.mjs read '<predecessor-id>' --json
```

질문에 필요한 section만 빠졌다면 `read '<id>' --section 'Rationale' --json`처럼 그 부분만 읽는다. 선택을 비교할 때는 Decision, Rationale, Rejected alternatives와 비어 있지 않은 Revisit conditions를 생략하지 않는다. 존재가 확인된 section만 선택하며 `--section`을 생략하면 실제 section 전체를 읽는다. byte 제한 결과의 `truncated:true`는 불완전한 본문이므로 전체 비교 근거로 쓰지 않는다.

`context-decision-compare/v1`은 위 판정 규칙과 `decision_cli.mjs schema --json`에 정의된 `context-decision-assessment/v1`을 참조한다. 이 skill이 없는 caller는 계약을 먼저 읽으며 매 호출 다시 읽지 않는다. `retrieval`·`warnings`를 확인한다. 조회는 선택된 scoped set만 다루며 필요한 근거가 빠졌으면 메타데이터나 명시한 ID를 읽고 전역 조회를 자동 반복하지 않는다.

추가 비교가 필요한 경우에만 `--known-current '<id>:<반환된-sha256>'`로 같은 scope/anchor context에 실제 비교 section 전체가 남은 기록을 전달한다(서로 다른 ID 최대 12개). `sha256:` 접두사를 그대로 복사한다. `comparison.current[].sections_ref`는 보유한 원문으로 복원한 뒤 비교·인용한다. 부분 read, context 소실, handoff 뒤에는 flag를 생략한다. 새 기록이나 변경된 파일은 본문을 반환한다. `hydrated_input_digest`는 완전한 비교 입력, `transport_digest`는 `comparison`을 결박하며 의미나 승인을 증명하지 않는다. 전체 의미 입력 제한도 유지한다. 기존 `check`와 full/delta 응답은 호환 경로로 남는다.

```bash
node /loaded/bobbin/skills/decision/scripts/decision_cli.mjs search \
  --query '<구별되는 검색어>' --scope '<scope>' --json

node /loaded/bobbin/skills/decision/scripts/decision_cli.mjs compare \
  --statement '<형성되거나 바뀌는 선택>' --scope '<scope>' --decision-key '<key>' \
  --known-current '<current-id>:<반환된-sha256>' --json
```

## Capture

공통 기록 정책을 따른다. Host inventory나 core doctor를 미리 실행하지 않는다. 사용자 승인에는 `record --approved`, 정책 승인에는 `record --approval-source policy`를 같은 응답에서 한 번 실행한다. adaptive는 record/ask 판정과 이유도 전달한다. internal preview가 동결한 receipt와 `approval_digest`를 변경 없이 apply한다. transport detail은 노출하거나 요구하지 않는다. semantic delta나 slot conflict면 write를 보류하고 그 차이만 확인한다. 승인 뒤 재생성하지 않는다. 성공 출력이 확인이며 이후 다시 읽지 않는다.

교체는 predecessor와 successor의 실제 본문·scope·rationale를 비교해 같은 선택 문제를 다룬다고 확인한 뒤에만 `record --supersede <current-id> --attest-same-claim`을 사용한다. 이 flag는 실제 비교 결과를 진술하며 승인·ID 일치·slot metadata로 대신할 수 없다. canonical scope와 decision key를 유지한다. 후속 없이 종료는 `record --withdraw <current-id> --reason <text>`, pending low-level receipt 폐기는 `reject --receipt-file <retained-path>`를 사용한다. History는 `do_not_follow`다. orchestration용 low-level 2단계 capture는 유지된다: `decision_workflow.mjs preview`(frozen receipt, preview stdout의 `approval_digest`) 뒤 `apply --approved-digest`.

Context-core의 active-language contract를 따른다. 사용자용 텍스트는 active language, machine-readable surface는 English를 쓰고 artifact prose는 의미 번역하지 않는다.

```bash
node /loaded/bobbin/skills/decision/scripts/decision_cli.mjs compare \
  --statement '<형성되거나 바뀌는 선택>' \
  --scope '<scope>' --decision-key '<key>' --json

node /loaded/bobbin/skills/decision/scripts/decision_workflow.mjs record \
  --host <codex|claude-code> --inline --approved \
  --title '<title>' --summary '<summary>' --scope '<scope>' \
  --decision-key '<key>' --commitment-evidence '<evidence>' \
  --sec-decision '<decision>' --sec-rationale '<rationale>' \
  --sec-alternatives '<rejected alternative>' --sec-revisit '<revisit condition>' \
  --attest-explicit-choice --attest-scope-identified --attest-commitment-present \
  --json
```

승인된 교체는 위 실제 비교를 마친 뒤 다음 예제를 사용한다.

```bash
node /loaded/bobbin/skills/decision/scripts/decision_workflow.mjs record \
  --host <codex|claude-code> --inline --approved \
  --supersede '<current-id>' --attest-same-claim \
  --title '<title>' --summary '<summary>' --scope '<scope>' \
  --decision-key '<key>' --commitment-evidence '<evidence>' \
  --sec-decision '<decision>' --sec-rationale '<rationale>' \
  --sec-alternatives '<rejected alternative>' --sec-revisit '<revisit condition>' \
  --attest-explicit-choice --attest-scope-identified --attest-commitment-present \
  --json
```

`/loaded/...`는 이 파일의 skill catalog 경로에서 푼다. core는 자동 해석하며 `core_cli_required` 뒤에만 `--core-cli <path>`를 준다. 입력은 literal이며 `@file`은 UTF-8, `@@literal`은 `@`를 보존한다. 제한: DEC decision 1,200 codepoint, common claim 2,000, owner input 8 KiB, envelope 16 KiB.
