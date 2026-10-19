"""encode_frame against hand-built byte vectors."""

from __future__ import annotations

import datetime as dt

import pytest

from badgemagic_ble.protocol.legacy import (
    MAX_FRAME_SIZE,
    Message,
    Mode,
    OemBrightness,
    chunk16,
    encode_frame,
    encode_pin,
    encode_timestamp,
)
from badgemagic_ble.render import Bitmap

TS = dt.datetime(2026, 10, 18, 19, 5, 42)


def one_chunk_bitmap() -> Bitmap:
    """8 columns: a diagonal from top-left plus a full rightmost column."""
    bm = Bitmap(8)
    for y in range(8):
        bm[y, y] = True
    for y in range(11):
        bm[7, y] = True
    return bm


# Rows top to bottom, MSB leftmost: diagonal bit 0x80 >> y, OR 0x01 for column 7.
ONE_CHUNK_BYTES = bytes([0x81, 0x41, 0x21, 0x11, 0x09, 0x05, 0x03, 0x01, 0x01, 0x01, 0x01])


def test_single_message_full_vector() -> None:
    msg = Message(one_chunk_bitmap(), Mode.SCROLL_LEFT, speed=1)
    frame = encode_frame([msg], TS)

    expected = bytearray(64)
    expected[0:5] = b"wang\x00"
    expected[5] = 0x00  # brightness
    expected[6] = 0x00  # flash
    expected[7] = 0x00  # marquee
    expected[8] = 0x00  # speed 1 -> nibble 0, mode 0
    expected[0x10:0x12] = b"\x00\x01"  # one chunk, big-endian
    expected[0x26:0x2C] = bytes([234, 10, 18, 19, 5, 42])  # 2026 % 256 == 234
    expected += ONE_CHUNK_BYTES
    expected += bytes(5)  # 64 + 11 = 75 -> pad to 80

    assert frame == bytes(expected)
    assert len(frame) == 80


def test_header_offsets_every_field() -> None:
    bitmaps = [Bitmap(8 * (i + 1)) for i in range(8)]  # 1..8 chunks
    modes = list(Mode)[:8]
    messages = [
        Message(bm, mode, speed=i + 1, flash=i % 2 == 0, marquee=i >= 6)
        for i, (bm, mode) in enumerate(zip(bitmaps, modes, strict=True))
    ]
    frame = encode_frame(messages, TS, OemBrightness.HALF)

    assert frame[0:5] == b"wang\x00"
    assert frame[5] == 0x20
    assert frame[6] == 0b01010101  # messages 0, 2, 4, 6 flash
    assert frame[7] == 0b11000000  # messages 6, 7 marquee
    assert list(frame[8:16]) == [(i << 4) | i for i in range(8)]
    assert frame[0x10:0x20] == b"".join((i + 1).to_bytes(2, "big") for i in range(8))
    assert frame[0x20:0x26] == bytes(6)
    assert frame[0x26:0x2C] == bytes([234, 10, 18, 19, 5, 42])
    assert frame[0x2C:0x30] == bytes(4)
    assert frame[0x30:0x40] == bytes(16)
    n = sum(range(1, 9))
    assert len(frame) == 64 + 11 * n + (-(64 + 11 * n) % 16)
    assert frame[64:] == bytes(len(frame) - 64)  # all bitmaps blank


def test_sizes_are_big_endian_above_255() -> None:
    msg = Message(Bitmap(8 * 300), Mode.SCROLL_LEFT)
    frame = encode_frame([msg], TS)
    assert frame[0x10:0x12] == bytes([0x01, 0x2C])  # 300
    assert frame[0x12:0x20] == bytes(14)


def test_mode_byte_speed_nibble() -> None:
    bm = Bitmap(1)
    assert encode_frame([Message(bm, Mode.LASER, speed=8)], TS)[8] == 0x78
    assert encode_frame([Message(bm, Mode.FIXED, speed=5)], TS)[8] == 0x44
    assert encode_frame([Message(bm, 7, speed=1)], TS)[8] == 0x07  # int mode coerced


def test_flash_marquee_bits_by_position() -> None:
    bm = Bitmap(1)
    msgs = [Message(bm), Message(bm, flash=True), Message(bm, marquee=True)]
    frame = encode_frame(msgs, TS)
    assert frame[6] == 0b010
    assert frame[7] == 0b100


def test_unused_message_slots_are_zero() -> None:
    frame = encode_frame([Message(Bitmap(1), Mode.PICTURE, speed=3)], TS)
    assert frame[8] == 0x27
    assert frame[9:16] == bytes(7)
    assert frame[0x12:0x20] == bytes(14)


def test_width_padded_to_whole_chunk() -> None:
    bm = Bitmap(3)
    bm[0, 0] = True
    bm[2, 10] = True
    frame = encode_frame([Message(bm)], TS)
    assert frame[0x10:0x12] == b"\x00\x01"
    data = frame[64:75]
    assert data[0] == 0x80
    assert data[10] == 0x20
    assert data[1:10] == bytes(9)


def test_data_order_follows_messages() -> None:
    a = Bitmap(8)
    a[0, 0] = True
    b = Bitmap(16)
    b[15, 10] = True
    frame = encode_frame([Message(a), Message(b)], TS)
    data = frame[64 : 64 + 33]
    assert data[0] == 0x80  # message 0 chunk 0 row 0
    assert data[11:22] == bytes(11)  # message 1 chunk 0
    assert data[22 + 10] == 0x01  # message 1 chunk 1 row 10 rightmost


def test_padding_to_16_and_exact_boundary() -> None:
    # 64 + 11 * 16 = 240 = 15 * 16: no padding needed.
    frame = encode_frame([Message(Bitmap(8 * 16))], TS)
    assert len(frame) == 240
    frame = encode_frame([Message(Bitmap(8 * 17))], TS)
    assert len(frame) == 256  # 251 -> 256
    assert frame[251:] == bytes(5)


def test_brightness_values() -> None:
    bm = Bitmap(1)
    assert encode_frame([Message(bm)], TS)[5] == 0x00
    for level, byte in ((OemBrightness.FULL, 0), (OemBrightness.THREE_QUARTERS, 0x10), (OemBrightness.QUARTER, 0x40)):
        assert encode_frame([Message(bm)], TS, level)[5] == byte


def test_timestamp_year_mod_256() -> None:
    assert encode_timestamp(dt.datetime(2026, 1, 2, 3, 4, 5)) == bytes([234, 1, 2, 3, 4, 5])
    assert encode_timestamp(dt.datetime(2048, 12, 31, 23, 59, 59))[0] == 0
    assert encode_timestamp(None) == bytes(6)
    # The open firmware decodes year = 2000 + (ts0 - 208) mod 256.
    for year in (2000, 2026, 2099, 2255):
        assert 2000 + (encode_timestamp(dt.datetime(year, 1, 1))[0] - 208) % 256 == year


def test_no_timestamp_is_zero() -> None:
    assert encode_frame([Message(Bitmap(1))], None)[0x26:0x2C] == bytes(6)


def test_message_count_limits() -> None:
    with pytest.raises(ValueError):
        encode_frame([], TS)
    with pytest.raises(ValueError):
        encode_frame([Message(Bitmap(1))] * 9, TS)


def test_frame_size_limit() -> None:
    chunks = (MAX_FRAME_SIZE - 64) // 11 + 1
    with pytest.raises(ValueError):
        encode_frame([Message(Bitmap(8 * chunks))], TS)


def test_message_validation() -> None:
    with pytest.raises(ValueError):
        Message(Bitmap(1), speed=0)
    with pytest.raises(ValueError):
        Message(Bitmap(1), speed=9)
    with pytest.raises(ValueError):
        Message(Bitmap(0))
    with pytest.raises(ValueError):
        Message(Bitmap(1), mode=9)


def test_chunk16() -> None:
    assert chunk16(b"") == []
    assert chunk16(b"\x01") == [b"\x01" + bytes(15)]
    data = bytes(range(40))
    packets = chunk16(data)
    assert [len(p) for p in packets] == [16, 16, 16]
    assert b"".join(packets) == data + bytes(8)
    frame = encode_frame([Message(one_chunk_bitmap())], TS)
    packets = chunk16(frame)
    assert len(packets) == 5
    assert packets[0].startswith(b"wang\x00")


def test_pin_packet() -> None:
    assert encode_pin("6512") == b"6512" + bytes(12)
    assert encode_pin(42) == b"0042" + bytes(12)
    for bad in ("123", "12345", "12a4", "١٢٣٤"):
        with pytest.raises(ValueError):
            encode_pin(bad)
