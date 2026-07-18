-- pytest 통합 테스트 전용 DB. 개발 DB(skhy_research)와 테이블/행을 공유하지 않는다.
-- /docker-entrypoint-initdb.d는 새 PostgreSQL data volume에서 한 번만 실행된다.
CREATE DATABASE skhy_research_test OWNER skhy;
