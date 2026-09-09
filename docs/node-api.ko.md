# TypeScript 코어와 CLI 사용

하나의 `@bobbin/context` 패키지에 코어, CLI, 타입 선언과 Agent plugin 진입점을
포함합니다. **Node.js 20.20.0 이상**이 필요합니다. 실행 시 Python, Git, Electron,
전역 설치나 플러그인 설치가 필요하지 않습니다. 버전은 **2.1.0**이며,
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
npm install /path/to/packages/bobbin-context-2.1.0.tgz
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
