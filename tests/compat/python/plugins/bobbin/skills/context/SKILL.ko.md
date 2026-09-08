---
name: context
description: 대화 delta를 audit하고 맥락을 recall해 성숙한 후보를 owner로 route한다.
---

# Context

[공통 기록 정책](references/recording-policy.md)을 따른다. 기능·승인은 `.bobbin/config.json`으로 정하며 사용자 승인은 explicit에 적용한다. auto/adaptive는 같은 검증 경로에 정책 승인을 전달한다.

vault: 설정된 프로젝트 vault, 없으면 가장 가까운 `context/` 상위 또는 cwd. 공통 기록 정책 외의 `references/`, manifest, `context/*.index.md`, plugin script는 읽지 않는다. DEC recall은 로드된 `decision` skill의 `decision_cli.py check --statement '<요청>' --json` 한 번이다. 다른 kind는 로드된 skill을 따른다. `--help`는 실행하지 않는다.

새 turn 의미만 한 번 audit한다. durable signal이 없으면 context tool call 0이고 audit 표시·capture 질문도 없다. 행동·계약 중립인 기계적 편집은 AGENTS/guidance 탐색을 생략하고 `context/`를 제외한다. 요청에 path가 있으면 그 target만 확인한다. 아니면 요청의 task noun으로 subtree 하나를 정해 한 번만 탐색하고 exact file만 쓴다. 파일 목록과 내용을 함께 검색한다. miss면 후보를 열고 subtree 부재면 관례 파일을 만든다. `.`, `--hidden`, repository-wide glob, repository root는 쓰지 않는다. 안전하지 않으면 범위를 넓히지 말고 path를 묻는다. `context/` artifact read 0, context 언급 0이다. scope·anchor, 본문이 남은 Current `{id,sha256}`, pending·dismissed·deferred 참조만 세션에 두며 저장·재제안하지 않는다.

1. 이전 맥락이 판단을 바꿀 때만 recall한다. 신호는 사용자 선택이며 요청 수행은 아니다. 조용한 index 확인→선택 본문 읽기→행동 변경 시 사용자 언급→필요한 질문으로 진행한다.
2. semantic owner가 실제 claim·section·scope·rationale를 비교한다. hash·ID·metadata는 의미 근거가 아니다. conflict나 rationale change를 먼저 알리고 행동을 보류한다. keep이면 수행하지 않고 supersede면 그 명시적 선택 뒤에만 진행한다. 조건 충족은 재평가 권한이지 구현 권한이 아니다. 그 선택이 decision payload를 확정하며 별도 저장 질문은 없다.
3. 원 요청을 먼저 마치고 성숙한 후보만 milestone당 제안한다. 설명되지 않는 interface failure 뒤에만 구현을 읽는다.
4. core만 owner, lifecycle, index, target bytes, vault identity, CAS, lock, frozen bundle 검증 뒤 쓴다.

공통 기록 정책으로 사용자 또는 정책 승인을 판정한다. 미확정 semantic delta만 묻고 저장 본문·`approval_digest`는 노출하지 않는다. 같은 응답에서 internal preview와 변경 없는 apply를 수행하며 승인 뒤 재생성하지 않는다. DEC는 `record` 한 번, 성공 출력이 확인이다.

사용자 텍스트는 active language, machine field는 English.
