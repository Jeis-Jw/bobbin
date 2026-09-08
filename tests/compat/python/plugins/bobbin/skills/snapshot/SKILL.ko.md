---
name: snapshot
description: 사용자가 명시적으로 요청할 때 unfinished 작업의 named SNAP handoff를 저장·갱신·조회·폐기하고, 이전 작업을 이어갈 때 SNAP을 load한다.
---

# Snapshot (한국어)

먼저 [공통 기록 정책](../context/references/recording-policy.md)을 따릅니다. 기능 활성화와 `explicit|auto|adaptive` 승인은 프로젝트의 `.bobbin/config.json`이 정합니다. 아래 사용자 승인 절차는 `explicit` 모드에 적용하며, 자동 모드에서는 같은 검증 경로에 정책 승인을 전달합니다. 기능이 꺼져 있으면 자동 참여와 새 기록을 중단하지만 명시적 과거 기록 읽기는 가능합니다. 의미 검증과 사용자 결정의 근거는 모든 모드에서 유지합니다.

SNAP은 `authority: staging`인 mutable resume context다. 결정·관찰의 권위 기록으로 취급하지 않는다. `references/`, plugin manifest, `context/*.index.md`, plugin script를 읽지 말고 `--help`도 실행하지 않는다. 아래 명령이 전부다. 사용자 문장은 활성 언어로, 기계 필드는 영어로 쓰고 사용자가 쓴 artifact 문장은 그대로 보존한다.

1. 재개: 사용자가 이전 작업을 다시 설명하지 않고 이어가자고 하면 `snapshot list` 한 번, 그다음 해당 SNAP을 `snapshot load --id <id>`로 읽고, 그 `다음 단계`·`열린 항목`과 명시된 제약을 따라 계속한다. SNAP에 이미 있는 내용을 사용자에게 다시 묻지 않는다.
2. 저장은 unfinished context와 명시적 handoff 의도가 모두 있을 때만 한다. `save`는 create-only이며 `현재 맥락`, `열린 항목`, `다음 단계`를 채운다. 정확한 다음 단계와 사용자가 준 제약을 적는다.
3. `update`는 기본 full replacement이고 일부만 바꿀 때만 `--merge`다. `load.freshness`는 warning일 뿐이다. `discard`는 SNAP 하나를 대상으로 한다. archive/history/retired 상태는 없다.

사용자가 snapshot content와 scope를 직접적·명시적·무조건적으로 확정해 handoff를 요청하면 semantic approval로 본다. 의미가 미확정일 때만 짧은 의미 질문을 한 번 하며, 단순 확인·칭찬·조건·수정 요청·화제 전환은 승인이 아니다. 저장 파일의 렌더링 본문을 보여주거나 별도 저장 승인을 묻지 않는다. 그다음 같은 응답에서 `save --approved`(또는 `update --approved`) 한 번을 실행한다. 내부 preview, receipt 동결, 무변경 apply가 한 번에 일어나며 transport detail은 비공개로 유지하고 승인 뒤 재생성하지 않는다. 렌더링이 의미를 더하거나 바꾸면 멈추므로 write를 보류하고 그 semantic delta만 확인한다. 명령 출력이 확인이며 파일을 다시 읽지 않는다. 어느 경로든 `approval_digest`는 내부에 머문다. 저수준 orchestration: `--approved` 없는 `save`는 write 0으로 frozen receipt를 돌려주며, `result.receipt_file`과 `result.approval_digest`를 그대로 `transaction apply --receipt-file ... --approved-digest ...`에 전달한다. receipt self-digest는 손상 검사일 뿐 승인 근거가 아니며 directory scan도 하지 않는다.

`/loaded/...`는 skill catalog에 있는 이 파일의 실제 경로로 바꾼다. 열린 항목과 다음 단계는 한 줄에 한 항목(`- ` 불릿은 선택, 항목당 240자 이내)이고 현재 맥락은 한 문단이다.

```bash
python3 /loaded/bobbin/skills/context/scripts/context_cli.py snapshot list --json
python3 /loaded/bobbin/skills/context/scripts/context_cli.py snapshot load --id '<id>' --json
python3 /loaded/bobbin/skills/context/scripts/context_cli.py snapshot save --approved \
  --title '<제목>' --summary '<요약>' --captured-from conversation \
  --attest-handoff-requested --attest-unfinished-context-present \
  --sec-context '<현재 맥락>' --sec-open-items '<열린 항목>' --sec-next-steps '<다음 단계>' --json
```
