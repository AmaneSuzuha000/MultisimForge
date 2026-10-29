"""Multisim XML reading/writing and common small utilities.

- Newlines in attribute values are part of SPICE models; they are converted to character references before parsing and restored on write-back.
- String attributes carry a type prefix: &ASC is ASCII; an empty string has no prefix.
"""
import copy
import itertools
import re
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from . import codec

_ATTR = re.compile(r'''(=\s*)(["'])(.*?)\2''', re.DOTALL)


def _preserve(match):
    value = match[3].replace("\r", "&#13;").replace("\n", "&#10;").replace("\t", "&#9;")
    return match[1] + match[2] + value + match[2]


def parse_text(text: str) -> ET.Element:
    # a few official examples have a NUL at the end of base64 attributes; XML does not allow it, and removing it does not affect the content
    text = text.replace("\x00", "")
    return ET.fromstring(_ATTR.sub(_preserve, text))


def _esc_attr(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")


def to_text(root: ET.Element) -> str:
    """Output in Multisim's own format: one tag per line, no indentation, newlines inside attributes preserved as-is."""
    out = ['<?xml version="1.0" encoding="ASCII"?>\n']

    def emit(el):
        attrs = "".join(f' {k}="{_esc_attr(v)}"' for k, v in el.attrib.items())
        text = (el.text or "").strip()
        if len(el) == 0 and not text:
            out.append(f"<{el.tag}{attrs}/>\n")
            return
        out.append(f"<{el.tag}{attrs}>")
        if text:
            out.append(text.replace("&", "&amp;").replace("<", "&lt;"))
        else:
            out.append("\n")
        for child in el:
            emit(child)
        out.append(f"</{el.tag}>\n")

    emit(root)
    return "".join(out)


def read(path) -> str:
    """Read text as-is (no newline conversion: \r\n inside attributes is SPICE model content)."""
    return Path(path).read_bytes().decode("utf-8")


def write(path, text: str) -> None:
    Path(path).write_bytes(text.encode("utf-8"))


def load(path) -> ET.Element:
    """Read a .ms14 or a decoded .xml and return the root element."""
    data = Path(path).read_bytes()
    if data.startswith(codec.MAGIC):
        data = codec.decode(data)
    return parse_text(data.decode("utf-8", errors="replace"))


def save(root: ET.Element, path) -> None:
    """Write .ms14 (compressed) or .xml depending on the extension."""
    data = to_text(root).encode("utf-8")
    path = Path(path)
    path.write_bytes(codec.encode(data) if path.suffix.lower() == ".ms14" else data)


def asc(value) -> str:
    return "&ASC" + str(value) if value not in (None, "") else ""


def unasc(value) -> str:
    value = value or ""
    return value[4:] if value.startswith("&ASC") else value


_UC = re.compile(r"_uc(\d)")


def decode_uc(text: str) -> str:
    """Multisim's non-ASCII escape: _uc<n><n 4-digit hex values>, e.g. _uc103a9 = Ω, and _uc26e2958c encodes two non-ASCII characters."""
    out, i = [], 0
    for m in _UC.finditer(text):
        if m.start() < i:
            continue
        n = int(m.group(1))
        hexes = text[m.end():m.end() + 4 * n]
        if n == 0 or len(hexes) < 4 * n or any(c not in "0123456789abcdefABCDEF" for c in hexes):
            continue
        out.append(text[i:m.start()])
        out.append("".join(chr(int(hexes[k:k + 4], 16)) for k in range(0, 4 * n, 4)))
        i = m.end() + 4 * n
    out.append(text[i:])
    return "".join(out)


def text(value: str) -> str:
    """Attribute string → plain text (both &ASC and &UNI are decoded)."""
    value = value or ""
    if value.startswith("&UNI"):
        return decode_uc(value[4:])
    return unasc(value)


def uni(value: str) -> str:
    """Plain text → attribute string: pure ASCII uses &ASC; otherwise &UNI with consecutive non-ASCII characters escaped in groups of at most 9."""
    if all(ord(ch) < 128 for ch in value):
        return asc(value)
    out, run = [], []

    def flush():
        while run:
            chunk = run[:9]
            del run[:9]
            out.append(f"_uc{len(chunk)}" + "".join(f"{ord(ch):04x}" for ch in chunk))
    for ch in value:
        if ord(ch) < 128:
            flush()
            out.append(ch)
        else:
            run.append(ch)
    flush()
    return "&UNI" + "".join(out)


def clear(element: ET.Element) -> None:
    for child in list(element):
        element.remove(child)


def find_tag(root: ET.Element, tag: str):
    return next(root.iter(tag), None)


class IdAllocator:
    """New object IDs: increment from a large number that does not clash with templates (Multisim only requires uniqueness within the file)."""

    def __init__(self, start: int = 900_000_000):
        self._next = itertools.count(start)

    def __call__(self) -> str:
        return str(next(self._next))


ID_ATTRS = ("ID", "CiID")
# attributes referencing other object IDs (remapped together)
REF_ATTRS = ("SymCompID", "CiComponent", "Component", "Connect1", "Connect2", "Node", "NodeText",
             "Model", "CiProbeExtComp", "Circuit", "Scope", "PortID")
GUID_ATTRS = ("Guid", "InstanceID")


def remap(items, ids: IdAllocator) -> dict:
    """Assign new IDs to a group of subtrees (the elements, ports, model, and symbol of one component), updating internal references together; returns old ID → new ID.

    References outside the group (the node a port connects to, the owning circuit) are also given new numbers and must be reset by the caller afterwards.
    """
    mapping = {}
    for item in items:
        for el in item.iter():
            for attr in ID_ATTRS:
                old = el.get(attr)
                if old and old not in mapping:
                    mapping[old] = ids()
    for item in items:
        for el in item.iter():
            for attr in ID_ATTRS + REF_ATTRS:
                old = el.get(attr)
                if old in mapping:
                    el.set(attr, mapping[old])
            for attr in GUID_ATTRS:
                if el.get(attr):
                    el.set(attr, "{" + str(uuid.uuid4()).upper() + "}")
    return mapping


def deepcopy(element: ET.Element) -> ET.Element:
    return copy.deepcopy(element)
