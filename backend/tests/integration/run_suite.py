"""Run the infrastructure integration suites against a live Compose stack."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[2]
POSTGRES_SUITE = "tests/integration/test_postgres_pgvector.py"
DOCKER_SUITE = "tests/integration/test_docker_stack.py"
HEALTH_URL = "http://localhost:8000/health/ready"


def _sync_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _assert_backend_ready() -> None:
    with urllib.request.urlopen(HEALTH_URL, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"Backend health check returned {response.status}")


def _ensure_test_database(database_url: str) -> str:
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql"):
        raise RuntimeError("DATABASE_URL must point to PostgreSQL for integration tests")

    database = f"{url.database or 'documind'}_test"
    test_url = url.set(database=database)
    admin_url = _sync_url(url.set(database="postgres").render_as_string(hide_password=False))
    with psycopg.connect(admin_url, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (database,),
        ).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    return test_url.render_as_string(hide_password=False)


def _run_pytest(path: str, environment: dict[str, str]) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "addopts=", "-q", "-rs", path],
        cwd=BACKEND_DIR,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        choices=("all", "postgres", "docker"),
        default="all",
        help="Limit execution to one integration suite.",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    _assert_backend_ready()

    if args.suite in ("all", "postgres"):
        test_database_url = os.environ.get("TEST_DATABASE_URL") or _ensure_test_database(
            database_url
        )
        print("Running PostgreSQL/pgvector integration suite...", flush=True)
        _run_pytest(
            POSTGRES_SUITE,
            os.environ | {"TEST_DATABASE_URL": test_database_url},
        )

    if args.suite in ("all", "docker"):
        print("Running Docker/Celery integration suite...", flush=True)
        _run_pytest(
            DOCKER_SUITE,
            os.environ
            | {
                "DOCKER_E2E_BASE_URL": os.environ.get(
                    "DOCKER_E2E_BASE_URL", "http://localhost:8000"
                ),
                "DOCKER_E2E_DATABASE_URL": os.environ.get("DOCKER_E2E_DATABASE_URL")
                or _sync_url(database_url),
            },
        )

    print("Integration suites passed.", flush=True)


if __name__ == "__main__":
    main()
