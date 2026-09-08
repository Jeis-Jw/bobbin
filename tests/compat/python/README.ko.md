# Bobbin

Keep the thread. 작업의 맥락을 이어갑니다.

[English](README.md)

## 무엇인가요?

Bobbin은 AI 코딩 세션이 바뀌어도 프로젝트의 결정과 이유, 확인된 사실,
미완료 작업, 가정, 용어, 의도와 현재 문서를 이어주는 단일 플러그인입니다.
기록은 로컬 `context/`의 Markdown 파일로 남습니다. Git은 선택 사항이며,
별도 서버·데이터베이스·API 키가 필요하지 않습니다.

## 설치

Bobbin 1.0.0은 Python 3.11 이상과 Codex 또는 Claude Code가 필요합니다.
소스 저장소는 [Jeis-Jw/bobbin](https://github.com/Jeis-Jw/bobbin)입니다.

```bash
# Codex
codex plugin marketplace add https://github.com/Jeis-Jw/bobbin.git
codex plugin add bobbin@bobbin

# Claude Code
claude plugin marketplace add https://github.com/Jeis-Jw/bobbin.git --scope user
claude plugin install bobbin@bobbin --scope user
```

기존 `context-*` 플러그인은 먼저 비활성화해 중복 실행을 막습니다.
설치 후 호스트를 다시 로드하거나 새 세션을 시작합니다.
로컬 개발 시에는 Git URL 대신 실제 Bobbin checkout 경로를 사용합니다.

## 사용

프로젝트에서 `$bobbin:init`을 실행해 사용할 기능과 승인 모드를 선택합니다.
init은 이미 설치된 코드를 프로젝트에 설정할 뿐, 플러그인을 설치하지 않습니다.

| 설정 | 선택 |
|---|---|
| 시맨틱 기능 | Decision, Assumption, Term, Intent, Document |
| 기본 제공 | Observation, Snapshot, Archive |
| 승인 모드 | `explicit`, `auto`, `adaptive` |

새 프로젝트는 Decision과 `explicit`으로 시작합니다. 기존 프로젝트는 등록된
기능을 가져오고 명시적 승인을 유지합니다. 재실행 시 생략한 설정은 보존하며,
기능을 끄더라도 과거 기록과 명시적 읽기는 유지됩니다. 자동 참여와 새 기록만
중단합니다.

- **explicit — 명시적 승인:** 명확한 결정 발언이나 기억 요청 자체가 승인입니다.
  의미·범위·기존 결정 변경이 불명확할 때만 확인합니다.
- **auto — 자동 기록:** 기록 가치가 있는 맥락을 건별 질문 없이 저장합니다.
- **adaptive — LLM 판단:** 의미의 명확성, 근거, 범위, 기존 기록과의 충돌,
  변경의 영향을 보고 바로 기록할지 물어볼지 판단합니다.

자동 기록은 대화 전문 저장이 아닙니다. 어떤 모드에서도 검토 중인 제안을
확정된 결정으로 바꾸거나 LLM의 선호를 사용자 결정으로 기록하지 않습니다.
기록 설정이 코드 수정·외부 전송·배포 권한을 부여하지도 않습니다.

설정 정본은 프로젝트의 `.bobbin/config.json`입니다. 생성되는
`AGENTS.md`/`CLAUDE.md` 지침과 기록 index는 별도 역할을 합니다.
여러 프로젝트가 vault를 공유해도 기능 선택과 승인 모드는 프로젝트마다 다릅니다.

### 두 세션에서 결정 하나 이어 보기

새 프로젝트에서 init을 마쳤다면(Decision + `explicit`), 다음 발화를 시도해 보세요.

1. 첫 세션에서 결정을 남깁니다.

   > 이 체험판은 CSV만 지원하고 Excel은 다음으로 미루기로 결정했어.
   > 첫 사용 흐름을 빨리 검증하려는 선택이야. 이 결정을 기억해 줘.

2. 같은 프로젝트의 새 세션에서 이유를 물어봅니다.

   > 우리가 Excel 지원을 미룬 이유가 뭐였지?

3. 이어서 변경 가능성을 검토해 봅니다.

   > Excel도 이번 체험판에 넣는 건 어떨까? 아직 결정 변경은 아니야.

기록 확인, 저장된 이유 회수, 기존 결정과의 비교가 이어지는지 확인해 보세요.
마지막 발화는 검토 요청이므로 기존 결정을 바꿀 승인이 되지 않습니다.
`auto`/`adaptive`에서도 제안은 확정된 결정으로 기록하지 않습니다.

“이 선택을 한 이유가 뭐였지?”, “이 결정을 기억해”, “어디까지 했는지 저장해”처럼
자연스럽게 사용합니다. 모든 모드에서 정확한 본문 결박과 안전한 쓰기 검증은
유지됩니다.

[마이그레이션](MIGRATION.md) · [기여 안내](CONTRIBUTING.md) ·
[보안 정책](SECURITY.md). Apache License 2.0.
