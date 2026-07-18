# 로컬 개발 환경 설정

## Python·패키지

```bash
uv python install 3.12   # 최초 1회
uv sync --group dev
```

## PostgreSQL 16

기본 경로는 `docker-compose.yml`의 `postgres` 서비스다.

```bash
docker compose up -d postgres
```

**환경 참고사항**: 이 저장소를 처음 구성한 에이전트 세션에는 Docker Desktop(또는 동등한
daemon)이 설치되어 있지 않았다. 해당 세션에서는 동일한 접속 정보
(`postgresql+psycopg://skhy:skhy_local_dev@localhost:5432/skhy_research`, PRD/`implementation_plan.md`
4.2의 PostgreSQL 16 결정과 동일 버전)로 Homebrew의 `postgresql@16`을 대신 사용했다.

```bash
brew install postgresql@16
brew services start postgresql@16
psql -U "$(whoami)" -d postgres -c "CREATE ROLE skhy LOGIN PASSWORD 'skhy_local_dev';"
psql -U "$(whoami)" -d postgres -c "CREATE DATABASE skhy_research OWNER skhy;"
```

두 방식 모두 `.env`의 `SKHY_DATABASE_URL`과 동일한 접속 문자열을 사용하므로 애플리케이션
코드·마이그레이션·테스트는 어느 쪽을 쓰든 변경이 필요 없다. Docker가 있는 환경에서는
`docker-compose.yml`을 우선 사용한다.

## 환경변수

```bash
cp .env.example .env
# 필요한 조회 전용 키만 채운다. 기본 SKHY_BROKER_MODE=paper는 변경하지 않는다.
```

## 명령 재현 (P0-01 완료조건)

```bash
uv run ruff check src tests
uv run pyright
uv run pytest
uv run skhy-research config-check
```

모두 실제 API 키 없이 통과해야 한다. `pytest`는 기본적으로 `smoke` 마커 테스트를
제외한다 (`pyproject.toml`의 `addopts = "-m 'not smoke'"`). smoke 테스트는 사용자가
조회 전용 키를 주입한 환경에서 `uv run pytest -m smoke`로 별도 실행한다.
