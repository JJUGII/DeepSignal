# AI_CONTEXT Workflow

`AI_CONTEXT/`는 여러 AI 프로젝트를 동시에 운영할 때 프로젝트별 장기 문맥, 현재 상태, TODO, 규칙, prompt/result history를 표준 구조로 보관하기 위한 폴더다.

## Structure

```text
AI_CONTEXT/
├── PROJECT_CONTEXT.md
├── CURRENT_STATUS.md
├── TODO.md
├── KNOWN_ISSUES.md
├── RULES.md
├── PROMPT_HISTORY.md
└── RESULT_HISTORY.md
```

## File Roles

- `PROJECT_CONTEXT.md`: 프로젝트 목적, 핵심 기능, 아키텍처, 기술 스택, 주요 디렉토리, 장기 목표.
- `CURRENT_STATUS.md`: 최근 완료 작업, 현재 진행 중, 마지막 테스트 상태, 브랜치/버전, 다음 우선순위.
- `TODO.md`: `HIGH`, `MEDIUM`, `LOW`, `BACKLOG` 기준 작업 목록.
- `KNOWN_ISSUES.md`: 알려진 문제, 재현 조건, 임시 해결 방법, 미해결 이유.
- `RULES.md`: 금지 사항, 코드 스타일, 안전 규칙, 테스트 규칙, architecture boundary.
- `PROMPT_HISTORY.md`: 날짜, 작업 목표, Cursor prompt 요약, 결과 상태를 append-only로 기록.
- `RESULT_HISTORY.md`: 날짜, 변경 파일, 테스트 결과, 남은 문제, 다음 작업 후보를 append-only로 기록.

## CLI Usage

현재 프로젝트:

```bash
python main.py init-context
```

다른 프로젝트:

```bash
python main.py init-context --project ./projects/deepsignal
```

여러 하위 프로젝트:

```bash
python main.py init-context --project ./projects --all-projects
```

## Bootstrap Behavior

`init-context`는 로컬 파일만 읽어 다음 정보를 추론한다.

- 프로젝트 이름
- 주요 언어
- `requirements.txt`, `pyproject.toml`, `package.json` 등 dependency 파일
- `src`, `app`, `lib`, `tests`, `docs` 등 주요 구조
- README 기반 목적 요약

기존 `AI_CONTEXT` 파일은 overwrite하지 않는다. 없는 Markdown 파일만 생성한다.

## Overmind Integration Direction

Overmind 또는 향후 Telegram/Slack 기반 AI Work Loop Controller는 모든 프로젝트에서 같은 파일명을 읽어 현재 상태와 다음 작업을 파악할 수 있다.

- 시작 시 `PROJECT_CONTEXT.md`, `CURRENT_STATUS.md`, `TODO.md`, `RULES.md`를 먼저 읽는다.
- 실행 후 `RESULT_HISTORY.md`에 결과를 append한다.
- 새 prompt나 작업 요청은 `PROMPT_HISTORY.md`에 요약해 축적한다.
- 자동 분석은 `KNOWN_ISSUES.md`와 `TODO.md`의 priority section을 우선 사용한다.

## Safety Boundary

- 기존 `AI_CONTEXT` 파일 overwrite 금지.
- source code, git, dependency, scheduler, shell script를 대상 프로젝트에 생성하지 않음.
- network 호출과 LLM API 호출 없음.
- destructive operation 없음.
- 허용 범위는 read-only project scan과 missing Markdown 생성이다.
