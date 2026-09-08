---
name: archive
description: durable context의 근거로 채택한 불변 장문 원본을 보존합니다.
---

# Context archive

먼저 [공통 기록 정책](../context/references/recording-policy.md)을 따릅니다. 기능 활성화와 `explicit|auto|adaptive` 승인은 프로젝트의 `.bobbin/config.json`이 정합니다. 아래 사용자 승인 절차는 `explicit` 모드에 적용하며, 자동 모드에서는 같은 검증 경로에 정책 승인을 전달합니다. 기능이 꺼져 있으면 자동 참여와 새 기록을 중단하지만 명시적 과거 기록 읽기는 가능합니다. 의미 검증과 사용자 결정의 근거는 모든 모드에서 유지합니다.

ARCHIVE는 context-core가 소유하는 `context-archive/v1` evidence입니다.

- durable context record의 근거로 채택한 불변 source original만 capture합니다. deliverable 저장소가 아니며 OBS·DEC·INTENT·DOCUMENT를 대신하지 않습니다.
- substantive `Content`, 하나 이상의 `source_ref`, source가 evidence로 채택됐고 불변 원본이 존재한다는 명시적 attestation을 요구합니다.
- source와 scope를 확정한 명시적 archive 요청 뒤 내부 `archive preview --content @file ...`가 semantic delta를 추가하지 않는지 확인하고, rendered body를 보여주지 않은 채 같은 응답에서 receipt와 approval digest를 바꾸지 않고 apply합니다.
- lifecycle은 capture·read·search·discard뿐이며 update·rename·retire·supersede는 없습니다.
- 기본 recall·pack에서는 제외합니다. 원본이 실제로 필요할 때만 `archive read|search` 또는 `recall --include-archive`를 사용합니다.
- OBS `Evidence` 항목은 exact ARCHIVE `ctx_` ID가 될 수 있습니다. 자유 문자열도 허용하며 exact ID는 context-core refresh가 검사합니다.
- inbound internal reference가 있는 archive는 discard하지 않습니다.

사용자-facing 문장은 context-core의 active language contract를 따르고 machine field는 English로 유지합니다. source·scope·capture effect를 확정한 직접적·명시적·무조건적 archive 요청이 semantic approval이며, 미확정 의미만 질문합니다. 단순 확인·조건·수정 요청·화제 전환은 승인이 아닙니다. 저장 파일의 rendered body를 보여주거나 별도 저장 승인을 묻지 않습니다. receipt 경로와 `approval_digest`는 비공개로 유지해 같은 응답의 apply에 그대로 전달합니다. 내부 preview에서 semantic delta가 드러나면 write를 보류하고 그 차이만 다시 확인합니다. 승인 뒤 재생성하지 않습니다.

[context-protocol.ko.md](../context/references/context-protocol.ko.md)를 참고합니다.
