"""A built design must be a real .ms14 container, not plain XML with an .ms14 name.

0.1.3 staged every build through a temporary file named ".rc.ms14.tmp-<run_id>".
xmlio.save picked the container from the file suffix, so the staged file was written
as plain XML and moved into place: Multisim could not open the result, and no test
noticed because every reader in the test suite accepted plain XML too.
"""
import json
from pathlib import Path

import pytest

from multisimforge import builder, codec, xmlio

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def spec(tmp_path):
    data = json.loads((ROOT / "examples" / "rc_lowpass.json").read_text(encoding="utf-8"))
    return dict(data, output=str(tmp_path / "rc.ms14"))


@pytest.mark.needs_parts
def test_build_writes_a_compressed_container(spec):
    out = Path(builder.build(spec)["ms14"])
    head = out.read_bytes()[:len(codec.MAGIC)]
    assert head == codec.MAGIC, (
        f"{out.name} is not a .ms14 container (starts with {head!r}); Multisim cannot open it")


@pytest.mark.needs_parts
def test_built_file_round_trips_through_the_codec(spec):
    out = Path(builder.build(spec)["ms14"])
    raw = out.read_bytes()
    assert codec.decode(raw)                                   # decodes without raising
    xmlio.load(out)                                            # and parses as a design


@pytest.mark.needs_parts
def test_repaired_build_is_also_a_container(tmp_path):
    """The repair path writes through a staging file too, so it needs the same guarantee."""
    data = json.loads((ROOT / "examples" / "rc_lowpass.json").read_text(encoding="utf-8"))
    # C1 sits on top of R1: only the auto-repair loop can route this, so the winner
    # is committed from a staging file rather than written straight to the output.
    # Drop a ground symbol into the corridor the router needs: a small, movable blocker,
    # which is exactly the case auto-repair is meant to resolve.
    data["layout"]["GND"] = list(data["layout"]["GND"]) + [[252, 216]]
    result = builder.build(dict(data, output=str(tmp_path / "repaired.ms14")))
    assert result["moved_parts"], "this layout should have needed a repair"
    out = Path(result["ms14"])
    assert out.read_bytes()[:len(codec.MAGIC)] == codec.MAGIC


def test_save_honours_an_explicit_format(tmp_path):
    """A staged write must not depend on the temporary file's suffix."""
    root = xmlio.parse_text('<?xml version="1.0" encoding="ASCII"?>\n<Root>\n<Item A="1"/>\n</Root>\n')
    staged = tmp_path / ".x.ms14.tmp-1234"
    xmlio.save(root, staged, compress=True)
    assert staged.read_bytes()[:len(codec.MAGIC)] == codec.MAGIC
    plain = tmp_path / "x.xml"
    xmlio.save(root, plain)
    assert not plain.read_bytes().startswith(codec.MAGIC)
