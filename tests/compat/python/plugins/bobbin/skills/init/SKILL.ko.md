---
name: init
description: 사용자가 현재 repository의 context-core 초기화나 bootstrap을 명시적으로 요청했을 때 canonical seed를 안전하고 멱등하게 적용한다. 일반 recall·capture 중에는 자동 실행하지 않는다.
---

# Init (한국어)

`--vault DIR`로 `context/`를 담는 기존 디렉터리를 선택하고 작업 전체에서 같은 vault를 사용한다. 생략하면 현재·상위 디렉터리 중 `context`가 있는 가장 가까운 곳, 없으면 현재 디렉터리를 사용한다. Git은 요구하지 않는다. Core·owner·workflow CLI는 subcommand 앞에, init adapter는 일반 옵션으로 `--vault`를 받는다. 입력 파일의 상대경로는 호출자 cwd 기준이다.

활성 host에 맞춰 `context_cli.py init --host codex --json` 또는 `context_cli.py init --host claude-code --json`을 한 번 호출한다. absent repository에는 canonical root/SNAP/OBS seed와 `codex → AGENTS.md`, `claude-code → CLAUDE.md` 관리형 policy를 설치한다. ready 재호출은 noop이다.

marker 밖 byte를 보존한다. broken/duplicate marker, symlink·nested target, incompatible schema/owner/path와 unsafe partial state는 write 0으로 실패한다. Addon bootstrap은 `context-owner-descriptor/v2`, 8 KiB 이하 canonical descriptor와 exact empty area seed를 요구한다. 등록된 descriptor identity는 immutable이며 자동 upgrade·downgrade·migration·delete·unknown trust repair를 하지 않는다. init 권한은 fixed `core_init|area_register|policy_install`뿐이다.

일반 durable capture는 rendered-file review가 아니라 semantic approval을 사용한다. 사용자가 payload·scope·lifecycle effect를 직접적·명시적·무조건적으로 확정하면 capture를 승인한 것이며, 미확정 의미만 짧게 질문한다. `알겠어` 단독, 조건, 수정 요청, 화제 전환은 승인이 아니다. 저장 본문을 보여주거나 별도 저장 승인을 묻지 않는다. 승인 뒤 내부 preview와 unchanged apply를 같은 응답에서 실행하고 digest·receipt 경로·내부 ID·core 경로는 비공개로 유지한다. preview에서 semantic delta가 드러나면 write를 보류하고 그 차이만 다시 확인한다. 승인 뒤 재생성하지 않는다.
