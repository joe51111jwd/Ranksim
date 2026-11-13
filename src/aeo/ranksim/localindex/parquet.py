"""Uncompressed Parquet (v1) for the local index.

The index source of truth is SQLite. These files are a columnar copy with
required PLAIN columns, so they can be read without pyarrow. Empty strings
and zeros stand in for missing values.
"""

from __future__ import annotations

import struct
from pathlib import Path

MAGIC = b"PAR1"


def zigzag(value: int) -> int:
    if value >= 0:
        return value << 1
    return ((-value) << 1) - 1


def uvarint(value: int) -> bytes:
    out = bytearray()
    number = value
    while True:
        byte = number & 0x7F
        number >>= 7
        if number:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _binary(text: str) -> bytes:
    raw = text.encode("utf-8")
    return uvarint(len(raw)) + raw


class _Compact:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.last = 0

    def _field(self, field_id: int, kind: int) -> None:
        delta = field_id - self.last
        self.last = field_id
        if 0 < delta <= 15:
            self.buf.append((delta << 4) | kind)
        else:
            self.buf.append(kind)
            self.buf.extend(uvarint(zigzag(field_id)))

    def i32(self, field_id: int, value: int) -> None:
        self._field(field_id, 5)
        self.buf.extend(uvarint(zigzag(value)))

    def i64(self, field_id: int, value: int) -> None:
        self._field(field_id, 6)
        self.buf.extend(uvarint(zigzag(value)))

    def string(self, field_id: int, value: str) -> None:
        self._field(field_id, 8)
        self.buf.extend(_binary(value))

    def struct(self, field_id: int, write) -> None:
        self._field(field_id, 12)
        parent = self.last
        self.last = 0
        write()
        self.buf.append(0)
        self.last = parent

    def _list(self, field_id: int, count: int, element_type: int) -> None:
        self._field(field_id, 9)
        if count <= 14:
            self.buf.append((count << 4) | element_type)
        else:
            self.buf.append(0xF0 | element_type)
            self.buf.extend(uvarint(count))

    def list_i32(self, field_id: int, values: list[int]) -> None:
        self._list(field_id, len(values), 5)
        for value in values:
            self.buf.extend(uvarint(zigzag(value)))

    def list_str(self, field_id: int, values: list[str]) -> None:
        self._list(field_id, len(values), 8)
        for value in values:
            self.buf.extend(_binary(value))

    def list_struct(self, field_id: int, items: list, write) -> None:
        self._list(field_id, len(items), 12)
        parent = self.last
        for item in items:
            self.last = 0
            write(item)
            self.buf.append(0)
        self.last = parent


def _plain(kind: str, values: list) -> bytes:
    body = bytearray()
    if kind == "string":
        for value in values:
            raw = str(value or "").encode("utf-8")
            body.extend(struct.pack("<I", len(raw)))
            body.extend(raw)
    elif kind == "int":
        for value in values:
            body.extend(struct.pack("<i", int(value or 0)))
    elif kind == "float":
        for value in values:
            body.extend(struct.pack("<d", float(value or 0.0)))
    else:
        raise ValueError(f"Unknown parquet column type {kind}")
    return bytes(body)


def _page(kind: str, values: list) -> bytes:
    body = _plain(kind, values)
    writer = _Compact()

    def data_header() -> None:
        writer.i32(1, len(values))
        writer.i32(2, 0)  # PLAIN
        writer.i32(3, 3)  # RLE definition levels (none are stored for required columns)
        writer.i32(4, 3)  # RLE repetition levels

    writer.i32(1, 0)  # DATA_PAGE
    writer.i32(2, len(body))
    writer.i32(3, len(body))
    writer.struct(5, data_header)
    writer.buf.append(0)
    return bytes(writer.buf) + body


def write_parquet(path: str | Path, columns: list[tuple[str, str]], rows: list[dict]) -> None:
    """Write one uncompressed row group. ``columns`` is (name, string|int|float)."""
    names = [name for name, _kind in columns]
    kinds = {name: kind for name, kind in columns}
    values = {name: [row.get(name) for row in rows] for name in names}
    pages = {name: _page(kinds[name], values[name]) for name in names}

    blob = bytearray(MAGIC)
    offsets: dict[str, int] = {}
    for name in names:
        offsets[name] = len(blob)
        blob.extend(pages[name])
    footer_start = len(blob)

    # Page header size is needed for column metadata. Recompute by parsing is
    # unnecessary: data_page_offset points at the page header, and the chunk
    # size is the whole page.
    writer = _Compact()
    writer.i32(1, 1)  # version

    def schema_item(spec: tuple[str, str] | None) -> None:
        if spec is None:
            writer.i32(3, 0)  # REQUIRED group
            writer.string(4, "schema")
            writer.i32(5, len(columns))
            return
        name, kind = spec
        type_code = {"string": 6, "int": 1, "float": 5}[kind]
        writer.i32(1, type_code)
        writer.i32(3, 0)  # REQUIRED
        writer.string(4, name)
        if kind == "string":
            writer.i32(6, 0)  # UTF8

    writer.list_struct(2, [None, *columns], schema_item)
    writer.i64(3, len(rows))

    def column_chunk(name: str) -> None:
        page = pages[name]
        writer.i64(2, offsets[name])

        def meta() -> None:
            type_code = {"string": 6, "int": 1, "float": 5}[kinds[name]]
            writer.i32(1, type_code)
            writer.list_i32(2, [3, 0])  # RLE, PLAIN
            writer.list_str(3, [name])
            writer.i32(4, 0)  # UNCOMPRESSED
            writer.i64(5, len(rows))
            writer.i64(6, len(page))
            writer.i64(7, len(page))
            # Field 8 is key_value_metadata. The data page offset is field 9.
            writer.i64(9, offsets[name])

        writer.struct(3, meta)

    def row_group(_ignored: int) -> None:
        writer.list_struct(1, names, column_chunk)
        writer.i64(2, sum(len(pages[name]) for name in names))
        writer.i64(3, len(rows))

    writer.list_struct(4, [0], row_group)
    writer.string(6, "ranksim-local-index")
    writer.buf.append(0)
    blob.extend(writer.buf)
    blob.extend(struct.pack("<I", len(blob) - footer_start))
    blob.extend(MAGIC)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(blob))


def _read_uvarint(data: bytes, index: int) -> tuple[int, int]:
    number = 0
    shift = 0
    while True:
        byte = data[index]
        index += 1
        number |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return number, index
        shift += 7


def _read_zigzag(data: bytes, index: int) -> tuple[int, int]:
    raw, index = _read_uvarint(data, index)
    value = (raw >> 1) ^ -(raw & 1)
    return value, index


def read_parquet(path: str | Path) -> list[dict]:
    """Read a file this module wrote. Other Parquet writers are not accepted."""
    data = Path(path).read_bytes()
    if not data.startswith(MAGIC) or not data.endswith(MAGIC):
        raise ValueError(f"{path} is not a parquet file")
    footer_len = struct.unpack_from("<I", data, len(data) - 8)[0]
    footer = data[len(data) - 8 - footer_len : len(data) - 8]
    # The writer stores columns in order. Walk the data pages between the
    # magic and the footer. Each page is a thrift header plus PLAIN values.
    # Column names and types come from the footer schema, which we decode
    # just far enough to recover names and physical types.
    columns = _schema_columns(footer)
    rows_n = _num_rows(footer)
    cursor = 4
    end = len(data) - 8 - footer_len
    decoded: dict[str, list] = {}
    for name, kind in columns:
        header, cursor = _skip_page_header(data, cursor)
        body_len = header
        body = data[cursor : cursor + body_len]
        cursor += body_len
        decoded[name] = _decode_plain(kind, body, rows_n)
        if cursor > end:
            raise ValueError(f"{path} ended inside column {name}")
    return [{name: decoded[name][index] for name, _kind in columns} for index in range(rows_n)]


def _schema_columns(footer: bytes) -> list[tuple[str, str]]:
    """Recover (name, kind) pairs from the schema list, skipping the root."""
    # FileMetaData field 1 is version, field 2 is the schema list.
    index = 0
    found: list[tuple[str, str]] = []
    # Walk fields until the schema list (field id 2, type LIST=9).
    last = 0
    while index < len(footer):
        if footer[index] == 0:
            break
        delta = footer[index] >> 4
        kind = footer[index] & 0x0F
        index += 1
        if delta == 0:
            field_id, index = _read_zigzag(footer, index)
        else:
            field_id = last + delta
        last = field_id
        if kind == 5:
            _value, index = _read_zigzag(footer, index)
        elif kind == 6:
            _value, index = _read_zigzag(footer, index)
        elif kind == 8:
            length, index = _read_uvarint(footer, index)
            index += length
        elif kind == 9 and field_id == 2:
            count, _element, index = _read_list_header(footer, index)
            for _ in range(count):
                name, physical, index = _read_schema_element(footer, index)
                if physical is not None and name != "schema":
                    found.append((name, physical))
            return found
        else:
            raise ValueError("Unsupported parquet footer while reading the schema")
    return found


def _read_list_header(data: bytes, index: int) -> tuple[int, int, int]:
    header = data[index]
    index += 1
    element = header & 0x0F
    count = header >> 4
    if count == 15:
        count, index = _read_uvarint(data, index)
    return count, element, index


def _read_schema_element(data: bytes, index: int) -> tuple[str, str | None, int]:
    last = 0
    name = ""
    physical: int | None = None
    while data[index] != 0:
        delta = data[index] >> 4
        kind = data[index] & 0x0F
        index += 1
        if delta == 0:
            field_id, index = _read_zigzag(data, index)
        else:
            field_id = last + delta
        last = field_id
        if kind == 5:
            value, index = _read_zigzag(data, index)
            if field_id == 1:
                physical = value
        elif kind == 8:
            length, index = _read_uvarint(data, index)
            text = data[index : index + length].decode("utf-8")
            index += length
            if field_id == 4:
                name = text
        else:
            raise ValueError("Unsupported schema element field")
    kind_name = {6: "string", 1: "int", 5: "float"}.get(physical or -1)
    return name, kind_name, index + 1


def _num_rows(footer: bytes) -> int:
    index = 0
    last = 0
    while index < len(footer) and footer[index] != 0:
        delta = footer[index] >> 4
        kind = footer[index] & 0x0F
        index += 1
        if delta == 0:
            field_id, index = _read_zigzag(footer, index)
        else:
            field_id = last + delta
        last = field_id
        if field_id == 3 and kind == 6:
            value, _index = _read_zigzag(footer, index)
            return value
        index = _skip_value(footer, index, kind)
    raise ValueError("Parquet footer has no row count")


def _skip_value(data: bytes, index: int, kind: int) -> int:
    if kind in {1, 2}:
        return index
    if kind in {5, 6}:
        _value, index = _read_zigzag(data, index)
        return index
    if kind == 8:
        length, index = _read_uvarint(data, index)
        return index + length
    if kind == 9:
        count, element, index = _read_list_header(data, index)
        return _skip_list(data, index, count, element)
    if kind == 12:
        return _skip_struct(data, index)
    raise ValueError(f"Cannot skip parquet type {kind}")


def _skip_list(data: bytes, index: int, count: int, element: int) -> int:
    for _ in range(count):
        if element == 12:
            index = _skip_struct(data, index)
        elif element == 5 or element == 6:
            _value, index = _read_zigzag(data, index)
        elif element == 8:
            length, index = _read_uvarint(data, index)
            index += length
        else:
            raise ValueError(f"Cannot skip parquet list element {element}")
    return index


def _skip_struct(data: bytes, index: int) -> int:
    last = 0
    while data[index] != 0:
        delta = data[index] >> 4
        kind = data[index] & 0x0F
        index += 1
        if delta == 0:
            _field_id, index = _read_zigzag(data, index)
        else:
            _field_id = last + delta
        last = _field_id
        index = _skip_value(data, index, kind)
    return index + 1


def _skip_page_header(data: bytes, index: int) -> tuple[int, int]:
    """Return (body length, index of the body)."""
    last = 0
    body_len = 0
    while data[index] != 0:
        delta = data[index] >> 4
        kind = data[index] & 0x0F
        index += 1
        if delta == 0:
            field_id, index = _read_zigzag(data, index)
        else:
            field_id = last + delta
        last = field_id
        if field_id == 3 and kind == 5:
            body_len, index = _read_zigzag(data, index)
        else:
            index = _skip_value(data, index, kind)
    return body_len, index + 1


def _decode_plain(kind: str, body: bytes, count: int) -> list:
    index = 0
    values: list = []
    for _ in range(count):
        if kind == "string":
            length = struct.unpack_from("<I", body, index)[0]
            index += 4
            values.append(body[index : index + length].decode("utf-8"))
            index += length
        elif kind == "int":
            values.append(struct.unpack_from("<i", body, index)[0])
            index += 4
        elif kind == "float":
            values.append(struct.unpack_from("<d", body, index)[0])
            index += 8
        else:
            raise ValueError(kind)
    return values
