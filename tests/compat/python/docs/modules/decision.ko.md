# Bobbin — decision

[English](./decision.md)

scope와 commitment evidence가 있는 사용자 결정을 보존한다. `Decision`, `Rationale`, `Rejected alternatives`, `Revisit conditions`의 실제 본문을 비교한다. 새 결정으로 교체하면 기존 기록은 `do_not_follow` 이력으로 남는다. 조건 충족은 재평가 권한이지 구현 권한이 아니다. 신규 구조는 canonical English이며 기존 `결정`, `취지`, `반려대안`은 legacy Korean read/round-trip alias다.

Bobbin 1.0.0에 포함된 내부 모듈이며 별도 플러그인이 아니다. 설치·설정은 [시작 안내](../../README.ko.md)와 단일 `$bobbin:init` 진입점을 사용한다. SNAP·OBS·ARCHIVE는 기본 제공하며 나머지 owner는 프로젝트에서 선택한다.

모든 기록은 [공통 기록 정책](../../plugins/bobbin/skills/context/references/recording-policy.md)의 `explicit|auto|adaptive`를 따른다. 기록 자동화는 의미 검증이나 사용자 의사결정을 대신하지 않는다. 비활성화해도 기존 데이터는 보존되며 명시적 이력 조회는 가능하다.

저장 구조·schema ID·protocol `context-common/v2`는 유지한다. 상세 필드, CLI와 lifecycle은 [프로토콜](../../plugins/bobbin/skills/decision/references/decision-protocol.md)을 따른다.
