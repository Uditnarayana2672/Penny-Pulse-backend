import os

import pytest

# Set before anything imports app.config, so lib and service tests need no real
# database and no real secret. Repository and router tests override DATABASE_URL with
# a Postgres that has ../penny-pulse-migrations applied — never create_all().
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-not-a-real-one")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
