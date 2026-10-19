"""Pure byte-level encoders for the Badge Magic BLE protocols (no I/O)."""

from . import legacy, ng
from .legacy import Message, Mode, OemBrightness, chunk16, encode_frame

__all__ = ["Message", "Mode", "OemBrightness", "chunk16", "encode_frame", "legacy", "ng"]
