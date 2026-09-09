# TypeScript 코어와 CLI 사용

하나의 `@bobbin/context` 패키지에 코어, CLI, 타입 선언과 Agent plugin 진입점을
포함합니다. **Node.js 20.20.0 이상**이 필요합니다. 실행 시 Python, Git, Electron,
전역 설치나 플러그인 설치가 필요하지 않습니다. 버전은 **2.3.0**이며,
소스 버전 갱신과 npm 공개 배포는 별개입니다.

호출한 OS 사용자가 쓸 수 있는 로컬 디렉터리를 사용합니다. 읽기에도 공통 잠금이
필요하며, 여러 프로세스의 접근을 조정합니다. 사용자 간 권한 관리 서비스는 아닙니다.

```sh
# Bobbin 저장소에서 패키지 생성
npm ci
npm test
npm run test:compat  # 개발용 Python 3.13 비교 검증
npm pack --pack-destination /path/to/packages

# 독립 소비자에서 실제 tarball 설치
npm install /path/to/packages/bobbin-context-2.3.0.tgz
npx --no-install bobbin init --vault /path/to/vault --features decision,intent,document
npx --no-install bobbin recall --vault /path/to/vault --query '저장소'
```

전역 CLI가 필요하면 같은 tarball을 `npm install --global`로 설치합니다.
플러그인은 자체 `dist/`를 호출하는 `.mjs` 진입점을 사용합니다. 별도 checkout이나
사용자별 설치 경로를 찾지 않습니다. [실행 가능한 소비자 예제](../examples/consumer.cjs)와
[타입/API 상세](node-api.md)에 라이브러리 ↔ CLI 양방향 예제가 있습니다.

```ts
import { createBobbin } from '@bobbin/context';
const bobbin = createBobbin({ vault: '/path/to/vault', project: '/path/to/project' });
const matches = await bobbin.recall({ query: '저장소', areas: ['decision'] });
const record = await bobbin.read(matches.items[0].id);
```

쓰기 흐름은 `createCandidate → createAttestation → preview → apply`입니다.
호출자가 실제 의미와 근거를 판단하며, 코어는 필드·본문·참조·생명주기와 승인 결박을
검증합니다. `explicit`은 `{source:'user'}`, `auto`는 `{source:'policy'}`, `adaptive`는
추가로 `decision:'record'`와 판단 이유가 필요합니다. 단순한 API 호출이나 해시는
사용자 승인을 대신하지 않습니다.

기존 8개 기록 유형, `context-common/v2`, ID·본문·범위·상태·대체 이력을 유지합니다.
조회에는 실제 본문과 authority/history 표시를 반환합니다. DEC 대체는 새 ID와 양방향
관계를 만들며, 같은 범위와 결정 키를 유지합니다. SNAP은 재개용이며 DEC와 혼용하지
않습니다. `batch`는 1–8개 작업을 원자적으로 적용하며, 같은 경로를 두 번 바꾸지는 못합니다.

`checkDecision({statement, scope?, decisionKey?, knownCurrent?})`는 서로 다른 ID 최대
12개의 `{id, sha256}` 배열을 선택적으로 받습니다. 이전 check의 파일 `sha256` 값을
`sha256:` 접두사까지 그대로 전달합니다(소문자 hex만 전달해도 허용).
CLI에서는 `decision check --known-current '<id>:<반환된-sha256>'`를 반복합니다.
해당 기록의 **실제 비교 section 전체**가 같은 scope/anchor context에 남아 있어야 합니다.
metadata·부분 read에서 힌트를 만들거나 context 소실·handoff 뒤 재사용하지 말고,
옵션을 생략해 전체 본문을 받습니다. hash 일치는 전송 재사용만 허용하며 의미 판정이나 승인이 아닙니다.

옵션이 없으면 기존 `context-decision-check/v1`은 그대로입니다. 옵션이 있으면
`context-decision-check-delta/v1`의 `comparison_delta`를 반환하며 내부 schema는
`context-decision-comparison-delta/v1`입니다. 변하지 않은 Current만 `sections`를
`sections_ref:{id,sha256}`로 대체합니다. 변경된 파일, 알 수 없는 힌트와 successor는
실제 section을 반환하고 proposal·path·선택 이유·lifecycle link는 최신 값을 유지합니다.
잘못된 힌트나 중복 ID는 `usage_invalid`로 거절합니다. 빈 배열도 delta 출력을 선택합니다.

delta에는 `comparison_input`·`input_digest`가 없습니다. `transport_digest`는
`canonicalDigest(result.comparison_delta)`입니다. `hydrated_input_digest` 검증은 각
`sections_ref`를 일치하는 보유 `sections`로 바꾸고 비교 schema를
`context-decision-comparison-input/v1`으로 되돌린 전체 입력에 `canonicalDigest`를 적용합니다.
보유 section이 하나라도 없으면 판정 전에 힌트 없이 다시 요청합니다. 두 digest는 내용 차이를
확인하며 쓰기를 승인하지 않습니다. 잠금 안에서의 최신 본문 읽기, 필수 선택, 전체 비교 입력
24,576바이트와 전체 결과 32,768바이트 제한은 투영 전에 유지하고 delta 결과도 32,768바이트로
제한합니다. `retrieval.body_reads`와 `selected_semantic_bytes`는 전체 check의 측정 의미를 유지합니다.

SNAP 생성·갱신에는 전체 논리적 입력(제목·요약·출처·참조·태그·검색어·앵커·렌더링된
섹션)의 compact JSON UTF-8 크기로 256 KiB(262,144바이트) 상한을 공통 적용합니다.
부분 갱신은 합쳐진 최종 내용을 검사합니다. 내용의 별도 글자 수·목록 개수·항목 길이
제한은 없으며 메타데이터 제한은 유지합니다. Markdown은 보존하고 CRLF만 LF로 바꿉니다.
CLI 저장·갱신은 `--sec-context @/path/to/context.md` 또는 원문 stdin을 받는
`--sec-context -`를 지원합니다. 여러 줄 목록 항목은 JSON 배열을 직접 전달하거나
`@/path/to/items.json`으로 읽습니다. 초과 오류 `snapshot_input_too_large`는
`actual_bytes`, `max_bytes`, `measurement: snapshot_payload_utf8`를 반환하고 기존 기록과
색인을 변경하지 않습니다. 자동 축약·분할하지 않으며 명시적 load/read는 읽기 상한을
지정하지 않으면 전체 내용을 반환합니다. search/recall 예산과 다른 기록 유형의 제한은 유지합니다. 기존 SNAP은
변환할 필요가 없지만, 구버전 runtime은 새 마커 형식이나 커진 SNAP을 제대로 읽지 못할 수 있습니다.

CLI는 stdout에 성공/오류 JSON 하나를 출력합니다. 종료 코드는 성공 0, 입력 2,
없음 3, 충돌·승인 5, 무결성 6, 예상 밖 실행 오류 1입니다. 라이브러리는 같은 오류를
`BobbinError`로 전달합니다. preview 이후 파일·참조·설정·실행 코드가 바뀌면 재검토가
필요합니다. 색인만 달라졌다면 기록에서 재생성합니다.

Bureau는 이 tarball을 production dependency로 포함하고, Electron의 Node 환경에서
라이브러리를 호출하면 됩니다. 큰 저장소는 파일 검증이 UI를 막지 않도록 Node worker나
utility process에서 처리하는 편이 좋습니다. writable vault는 ASAR 밖에 둡니다.
Bureau의 프로젝트/작업/에이전트 범위 규칙, 승인 UI, IPC, 충돌 처리와 최종 앱 배포
검증은 후속 통합 작업입니다. 이번 포팅은 Bureau 파일을 변경하지 않습니다.

Python과의 전환 절차, 검증 범위와 제한은 [호환성 문서](compatibility.md)를 확인하세요.

## 범위를 지정한 결정 비교

좌표를 모르면 `search('decision', {query, scope})`로 메타데이터를 찾고
`compareDecision({statement, scope, decisionKey, rationale?, query?, limit?, knownCurrent?})`로 비교한다.
CLI는 `decision search --query <검색어> --scope <scope>` 뒤
`decision compare --statement <선택> --scope <scope> --decision-key <key>`다.
좌표나 유효한 비교 결과를 알고 있으면 불필요한 호출을 생략한다. 빈 검색 결과를 무충돌로
판단하거나 전역 검색으로 자동 확대하지 않는다.

`DecisionCompareOptions`의 scope와 decisionKey는 필수다. 같은 scope와 상·하위 scope에서
같은 key의 필수 대상을 모두 포함하고, 선택적 본문은 그 범위에서 구별되는 메타데이터 일치가 있어야 한다.
범위만 같으면 본문을 읽지 않는다. 기본 8개·최대 12개, 복원된 비교 입력 24 KiB·응답 32 KiB 제한을
유지하며 필수 본문 초과는 생략 대신 실패한다.

단일 `context-decision-compare/v1` 응답은 `coverage:exact_slot`, `comparison`(proposal/current),
`deterministic`, `current_links`, `retrieval`, `warnings`, `assessment_contract_ref`, 두 digest와
`physical_write:false`를 반환한다. `total_current`는 전체, `scoped_current`·`outside_scope`·`omitted`·
`full_scoped_set`는 범위 제한을 설명한다. `body_reads`는 byte 제한으로 빠진 선택적 본문까지 실제 읽기를 센다.
정적 판단 계약은 `decision schema`의 `comparison_contract`에 있으며 skill이 없으면 먼저 한 번 읽는다.

`knownCurrent`는 기존 ID·전체 파일 SHA 힌트(최대 12개)를 재사용한다. 현재 context에 완전한 실제 section이
남은 기록만 참조로 받을 수 있다. 변경·신규 본문은 반환하고 부분 read·맥락 소실·handoff 뒤에는 힌트를 생략한다.
`comparison`의 `sections_ref`를 복원하고 schema를 `context-decision-comparison-input/v1`으로 바꾼 값이
`hydrated_input_digest` 대상이다. `transport_digest`는 실제 `comparison`을 해시한다.
기존 `checkDecision`·`decision check`의 후보 선택과 full/delta 형식은 공통 구현의 호환 경로로 유지한다.
