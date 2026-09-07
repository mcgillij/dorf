"""Tests for bot.utilities.split_message and bot.constants.level_threshold."""

from bot.constants import LEVEL_THRESHOLDS, level_threshold
from bot.utilities import split_message


def test_short_message_is_single_chunk():
    assert split_message("hello") == ["hello"]


def test_empty_message_returns_no_chunks():
    assert split_message("") == []


def test_long_message_chunks_bounded_and_lossless():
    text = "abcdefghij" * 25  # 250 chars
    chunks = split_message(text, max_length=50)
    assert len(chunks) == 5
    assert all(len(chunk) <= 50 for chunk in chunks)
    # split_message slices, it never drops characters — rejoining == original.
    assert "".join(chunks) == text


def test_uneven_tail_chunk():
    chunks = split_message("x" * 123, max_length=50)
    assert [len(c) for c in chunks] == [50, 50, 23]
    assert "".join(chunks) == "x" * 123


def test_level_threshold_formula():
    for lvl in range(0, 101):
        assert level_threshold(lvl) == 5 * lvl**2 + 50 * lvl + 100
    assert level_threshold(0) == 100
    assert level_threshold(10) == 1100


def test_level_thresholds_increase():
    thresholds = [level_threshold(lvl) for lvl in range(0, 51)]
    assert all(b > a for a, b in zip(thresholds, thresholds[1:]))


def test_level_thresholds_alias():
    assert LEVEL_THRESHOLDS is level_threshold
