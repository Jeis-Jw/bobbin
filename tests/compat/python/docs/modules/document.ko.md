# Bobbin — document

[English](./document.md)

현재 상태를 종합한 프로젝트 문서를 관리한다. DEC가 `affects:document`로 연결되고 해당 Current DEC가 더 최신이면 refresh가 `document-stale-vs-decision`을 경고한다. 원래 결정을 다시 쓰지 않고 문서를 갱신한다.

Bobbin 1.0.0에 포함된 내부 모듈이며 별도 플러그인이 아니다. 설치·설정은 [시작 안내](../../README.ko.md)와 단일 `$bobbin:init` 진입점을 사용한다. SNAP·OBS·ARCHIVE는 기본 제공하며 나머지 owner는 프로젝트에서 선택한다.

모든 기록은 [공통 기록 정책](../../plugins/bobbin/skills/context/references/recording-policy.md)의 `explicit|auto|adaptive`를 따른다. 기록 자동화는 의미 검증이나 사용자 의사결정을 대신하지 않는다. 비활성화해도 기존 데이터는 보존되며 명시적 이력 조회는 가능하다.

저장 구조·schema ID·protocol `context-common/v2`는 유지한다. 상세 필드, CLI와 lifecycle은 [프로토콜](../../plugins/bobbin/skills/document/references/document-protocol.md)을 따른다.
