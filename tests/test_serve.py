"""Container entry point: migrate-on-start retry logic (no DB needed)."""

import psycopg

from canesat.line.serve import migrate_with_retry


def test_retries_until_db_is_ready():
    calls = []

    def migrate(dsn):
        calls.append(dsn)
        if len(calls) < 3:
            raise psycopg.OperationalError("starting up")
        return ["0001_init.sql"]

    slept = []
    assert migrate_with_retry("db", migrate=migrate, sleep=slept.append, delay_s=1) == [
        "0001_init.sql"
    ]
    assert len(calls) == 3
    assert slept == [1, 1]


def test_gives_up_without_raising():
    def migrate(dsn):
        raise psycopg.OperationalError("down")

    assert migrate_with_retry("db", attempts=2, migrate=migrate, sleep=lambda s: None) is None
