"""Tests for bot.audio_capture.RingBuffer."""

import pytest

from bot.audio_capture import RingBuffer


def test_write_then_read_all_roundtrip():
    rb = RingBuffer(1024)
    rb.write(b"hello world")
    assert rb.read_all() == b"hello world"
    assert rb.is_empty()


def test_wrap_around_returns_chronological_data():
    rb = RingBuffer(16)
    rb.write(b"A" * 10)
    rb.write(b"B" * 6)  # write_ptr wraps past the seam
    assert rb.read_all() == b"A" * 10 + b"B" * 6


def test_wrap_after_drain_is_chronological():
    rb = RingBuffer(10)
    rb.write(b"first")
    assert rb.drain() == b"first"
    rb.write(b"second!")
    assert rb.read_all() == b"second!"


def test_overflow_keeps_only_newest_tail():
    rb = RingBuffer(8)
    data = b"1234567890123"  # 13 bytes > capacity
    rb.write(data)
    assert rb.read_all() == data[-8:]


def test_overflow_discards_oldest_unread_and_keeps_order():
    rb = RingBuffer(8)
    rb.write(b"A" * 6)
    rb.write(b"B" * 6)  # drops the 4 oldest unread bytes
    assert rb.read_all() == b"AA" + b"B" * 6


def test_post_drain_write_survives():
    rb = RingBuffer(64)
    rb.write(b"AAAA")
    assert rb.drain() == b"AAAA"
    # Audio captured during a flush must land in a fresh buffer and survive.
    rb.write(b"BBBB")
    assert rb.read_all() == b"BBBB"


def test_drain_resets_to_empty():
    rb = RingBuffer(64)
    rb.write(b"data")
    rb.drain()
    assert rb.read_all() == b""
    assert rb.is_empty()


def test_repeated_write_read_cycles():
    rb = RingBuffer(7)
    for i in range(50):
        payload = bytes([i % 256]) * 9  # overflows capacity each cycle
        rb.write(payload)
        assert rb.read_all() == payload[-7:]


@pytest.mark.parametrize("size", [1, 2, 3, 17])
def test_oversized_write_exactly_capacity(size):
    rb = RingBuffer(size)
    rb.write(b"\x01" * (size * 3))
    assert len(rb.read_all()) == size
