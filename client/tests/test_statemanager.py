"""Tests for bot.statemanager (module-level update_state/get_current_state)."""

import sqlite3

import pytest

import bot.statemanager as statemanager


@pytest.fixture
def state_db(tmp_path, monkeypatch):
    """Point statemanager at a throwaway sqlite db and create the table."""
    db_path = str(tmp_path / "avatar_state.db")
    monkeypatch.setattr(statemanager, "AVATAR_STATE_DB_PATH", db_path)
    statemanager._initialize_database(db_path)
    return db_path


def _row_count(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM avatar_state").fetchone()[0]


def test_update_then_get_state(state_db):
    statemanager.update_state("talking")
    assert statemanager.get_current_state() == "talking"


def test_plain_string_states_accepted(state_db):
    statemanager.update_state("thinking")
    statemanager.update_state("drawing")
    assert statemanager.get_current_state() == "drawing"


def test_table_pruned_to_100_rows(state_db):
    for i in range(150):
        statemanager.update_state("talking" if i % 2 else "idle")
    assert _row_count(state_db) <= 100


def test_prune_keeps_most_recent_state(state_db):
    statemanager.update_state("idle")
    for _ in range(120):
        statemanager.update_state("thinking")
    statemanager.update_state("talking")
    assert statemanager.get_current_state() == "talking"
