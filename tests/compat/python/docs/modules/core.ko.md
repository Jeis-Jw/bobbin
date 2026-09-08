# Bobbin — core

[English](./core.md)

저장·색인·라우팅·lifecycle 검증과 단일 쓰기 경로를 담당한다. SNAP은 미완료 작업 재개, OBS는 재사용할 근거, ARCHIVE는 변경하지 않는 원문을 보존한다. OBS나 SNAP을 사용자 결정으로 승격하지 않는다.

Bobbin 1.0.0에 포함된 내부 모듈이며 별도 플러그인이 아니다. 설치·설정은 [시작 안내](../../README.ko.md)와 단일 `$bobbin:init` 진입점을 사용한다. SNAP·OBS·ARCHIVE는 기본 제공하며 나머지 owner는 프로젝트에서 선택한다.

모든 기록은 [공통 기록 정책](../../plugins/bobbin/skills/context/references/recording-policy.md)의 `explicit|auto|adaptive`를 따른다. 기록 자동화는 의미 검증이나 사용자 의사결정을 대신하지 않는다. 비활성화해도 기존 데이터는 보존되며 명시적 이력 조회는 가능하다.

저장 구조·schema ID·protocol `context-common/v2`는 유지한다. 상세 필드, CLI와 lifecycle은 [프로토콜](../../plugins/bobbin/skills/context/references/context-protocol.md)을 따른다.
