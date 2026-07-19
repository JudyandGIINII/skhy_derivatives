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
psql -U "$(whoami)" -d postgres -c "CREATE DATABASE skhy_research_test OWNER skhy;"
```

Docker의 새 data volume은 `docker/postgres/init-test-database.sql`을 통해 개발 DB
`skhy_research`와 별도로 `skhy_research_test`를 자동 생성한다. 기존 Docker volume이나
Homebrew PostgreSQL에서 테스트 DB가 아직 없고 `skhy` 역할에 `CREATEDB` 권한도 없다면,
위의 마지막 명령을 로컬 관리자 역할로 한 번 실행한다. Docker가 있는 환경에서는
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

### PostgreSQL 테스트 격리

통합 테스트 fixture는 애플리케이션의 `SKHY_DATABASE_URL`을 사용하지 않고
`SKHY_TEST_DATABASE_URL`만 사용한다. 기본값은 다음과 같다.

```bash
SKHY_TEST_DATABASE_URL=postgresql+psycopg://skhy:skhy_local_dev@localhost:5432/skhy_research_test
```

다른 로컬·CI 테스트 DB를 쓰려면 이 환경변수만 override한다. 지정한 DB 이름이 기본 개발
DB `skhy_research`이거나 현재 `SKHY_DATABASE_URL`의 DB 이름과 같으면, 테이블 정리 전에
pytest가 즉시 실패한다. 테스트 DB에 연결할 수 없으면 PostgreSQL 통합 테스트는 skip된다.

```bash
SKHY_DATABASE_URL=postgresql+psycopg://skhy:skhy_local_dev@localhost:5432/skhy_research \
SKHY_TEST_DATABASE_URL=postgresql+psycopg://skhy:skhy_local_dev@localhost:5432/skhy_research_test \
uv run pytest
```
