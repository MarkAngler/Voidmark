"""Escape-framed node stream reader for Tibia .otbm and .otb files.

Byte framing (see sources/fileloader.cpp):
    0xFE = NODE_START
    0xFF = NODE_END
    0xFD = ESCAPE_CHAR (next byte is literal)

File layout:
    [4-byte identifier][node tree starting with 0xFE ... 0xFF]

Node layout after NODE_START:
    <1 byte: type>
    <props bytes, with 0xFD escaping for literal 0xFE/0xFF/0xFD>
    <zero or more child nodes, each framed by 0xFE..0xFF>
    <0xFF terminating this node>

Props always come before any child nodes inside a single node.
"""

import struct


NODE_START = 0xFE
NODE_END = 0xFF
ESCAPE_CHAR = 0xFD


class PropReader:
    """Reads fixed-width integers and length-prefixed strings from a node's
    unescaped props bytes. Mirrors PropStream from sources/fileloader.h."""

    __slots__ = ("buf", "pos", "size")

    def __init__(self, buf):
        self.buf = buf
        self.pos = 0
        self.size = len(buf)

    def remaining(self):
        return self.size - self.pos

    def eof(self):
        return self.pos >= self.size

    def skip(self, n):
        if self.pos + n > self.size:
            raise ValueError(f"PropReader skip out of bounds ({self.pos}+{n}>{self.size})")
        self.pos += n

    def u8(self):
        if self.pos + 1 > self.size:
            raise ValueError("PropReader u8 EOF")
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def u16(self):
        if self.pos + 2 > self.size:
            raise ValueError("PropReader u16 EOF")
        v = struct.unpack_from("<H", self.buf, self.pos)[0]
        self.pos += 2
        return v

    def u32(self):
        if self.pos + 4 > self.size:
            raise ValueError("PropReader u32 EOF")
        v = struct.unpack_from("<I", self.buf, self.pos)[0]
        self.pos += 4
        return v

    def string(self):
        """Length-prefixed (u16) string."""
        n = self.u16()
        if self.pos + n > self.size:
            raise ValueError("PropReader string EOF")
        s = self.buf[self.pos:self.pos + n].decode("latin-1", "replace")
        self.pos += n
        return s

    def raw(self, n):
        if self.pos + n > self.size:
            raise ValueError("PropReader raw EOF")
        v = bytes(self.buf[self.pos:self.pos + n])
        self.pos += n
        return v


def _read_props(data, pos):
    """Read and unescape a node's props section starting at `pos`.
    Stops when it encounters an unescaped 0xFE (child node) or 0xFF (node end).
    Returns (unescaped_props_bytes, new_pos) — new_pos points at the 0xFE or 0xFF.
    """
    n = len(data)
    out = bytearray()
    while pos < n:
        b = data[pos]
        if b == ESCAPE_CHAR:
            pos += 1
            out.append(data[pos])
            pos += 1
        elif b == NODE_START or b == NODE_END:
            return out, pos
        else:
            out.append(b)
            pos += 1
    raise ValueError("Unterminated node props")


def iter_nodes(data, start_pos=4):
    """Iterate a node tree from `data` starting at byte offset `start_pos`
    (which should be the 4-byte file-identifier offset).

    Yields:
        ("enter", type_byte, PropReader)   — props available only until the next yield
        ("leave", type_byte, None)

    The root node is yielded first. Emission order is depth-first.
    """
    n = len(data)
    pos = start_pos
    if pos >= n or data[pos] != NODE_START:
        raise ValueError(f"Expected NODE_START (0xFE) at offset {pos}, got 0x{data[pos]:02x}")
    pos += 1

    stack = []  # list of node type bytes
    # enter root
    ntype = data[pos]; pos += 1
    props, pos = _read_props(data, pos)
    stack.append(ntype)
    yield ("enter", ntype, PropReader(props))

    while stack:
        if pos >= n:
            raise ValueError("Unexpected EOF in node tree")
        b = data[pos]; pos += 1
        if b == NODE_START:
            ntype = data[pos]; pos += 1
            props, pos = _read_props(data, pos)
            stack.append(ntype)
            yield ("enter", ntype, PropReader(props))
        elif b == NODE_END:
            ntype = stack.pop()
            yield ("leave", ntype, None)
        else:
            raise ValueError(f"Unexpected byte 0x{b:02x} at offset {pos-1} (stack depth {len(stack)})")


def load_file_bytes(path):
    """Read the whole file into memory as bytes."""
    with open(path, "rb") as f:
        return f.read()
