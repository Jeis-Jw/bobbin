---
name: observation
description: 작업 중 발견한 사실·근거·시행착오가 이후에도 재사용될 가치가 있지만 authoritative decision은 아닐 때 비권위 OBS로 preview, 조회, 교정, 재검증, 무효화, supersede 또는 폐기한다.
---

# Observation (한국어)

먼저 [공통 기록 정책](../context/references/recording-policy.md)을 따릅니다. 기능 활성화와 `explicit|auto|adaptive` 승인은 프로젝트의 `.bobbin/config.json`이 정합니다. 아래 사용자 승인 절차는 `explicit` 모드에 적용하며, 자동 모드에서는 같은 검증 경로에 정책 승인을 전달합니다. 기능이 꺼져 있으면 자동 참여와 새 기록을 중단하지만 명시적 과거 기록 읽기는 가능합니다. 의미 검증과 사용자 결정의 근거는 모든 모드에서 유지합니다.

OBS는 `authority: evidence`인 immutable semantic claim이다. DEC처럼 따를 결정으로 표현하지 않는다.

1. capture에는 substantive `관찰`과 최소 한 개의 substantive `근거`가 필요하며 capability descriptor만 사용한다.
2. `annotate`는 metadata만 바꾼다. claim/evidence 의미가 달라지면 양쪽 실제 claim을 준비해 successor OBS와 predecessor History를 한 final bundle로 `supersede`한다.
3. 반증은 이유가 있는 `invalidate`, 실제 재확인은 evidence ref가 있는 `reverify`다. 오래됨만으로 retire하지 않는다.
4. ID/path, backlink, vault identity, CAS, lock, atomic-write guard를 유지한다. preview·prepare·attestation은 write 0이다.

사용자가 observation·scope·capture effect를 직접적·명시적·무조건적으로 확정하면 semantic approval로 본다. 의미가 미확정일 때만 짧은 의미 질문을 한 번 하며, 단순 확인·칭찬·조건·수정 요청·화제 전환은 승인이 아니다. 저장 파일의 렌더링 본문을 보여주거나 별도 저장 승인을 묻지 않는다. 승인 뒤 내부 preview가 semantic delta를 추가하지 않는지 확인하고 receipt 경로와 `approval_digest`를 그대로 같은 응답의 apply에 전달한다. transport detail은 비공개로 유지한다. delta가 있으면 write를 보류하고 그 차이만 다시 확인한다. 승인 뒤 재생성하지 않는다. receipt self-digest는 손상 검사일 뿐 승인 근거가 아니며 directory scan도 하지 않는다. 성공 시 receipt를 지우고 cleanup-only warning이면 이미 기록됐으므로 재시도하지 않는다.

CLI는 `../context/scripts/context_cli.py observation ...`을 사용하고 물리 write는 context-core만 수행한다.

```bash
python3 /loaded/bobbin/skills/context/scripts/context_cli.py observation preview --title '<제목>' --summary '<요약>' --captured-from workspace --attest-reusable-observation --attest-evidence-present --sec-observation '<관찰>' --sec-evidence '<근거>' --json
python3 /loaded/bobbin/skills/context/scripts/context_cli.py transaction apply --receipt-file '<agent가 유지한 result.receipt_file>' --approved-digest '<agent가 유지한 result.approval_digest>' --json
```

`observation capture`는 deprecated compatibility alias로 유지한다. 두 preview 명령 모두 `applied:false`, `state:"awaiting_approval"`을 반환하며 `transaction apply`가 성공하기 전에는 OBS가 기록되지 않는다.
