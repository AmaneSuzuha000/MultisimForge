"""The part library is a directory of files plus an index, so it can be tested as one.

A wrong lookup here fails much later as a missing part in the middle of a build, and
the error message is the only thing a user has to work with.
"""
import json

import pytest

from multisimforge import parts


def test_a_missing_library_is_empty_not_an_error(tmp_path):
    lib = parts.Library(tmp_path / "library")
    assert lib.index == {}
    assert list(lib.search()) == []


def test_an_existing_index_is_loaded(tmp_path):
    path = tmp_path / "library"
    path.mkdir()
    (path / "index.json").write_text(json.dumps({
        "2N3904": {"family": "TRANSISTOR", "file": "2N3904.xml"},
        "LM7805CT": {"family": "REGULATOR", "file": "LM7805CT.xml"},
    }), encoding="utf-8")
    lib = parts.Library(path)
    assert set(lib.index) == {"2N3904", "LM7805CT"}


def test_resolve_is_case_insensitive(tmp_path):
    lib = parts.Library(tmp_path / "library")
    lib.index = {"2N3904": {"family": "TRANSISTOR", "file": "2N3904.xml"}}
    lib._lower = {"2n3904": "2N3904"}
    assert lib.resolve("2n3904") == "2N3904"
    assert lib.resolve("2N3904") == "2N3904"
    assert lib.resolve("missing") is None


def test_loading_an_unknown_part_names_the_search_command(tmp_path):
    lib = parts.Library(tmp_path / "library")
    with pytest.raises(KeyError) as info:
        lib.load("NOSUCHPART")
    message = str(info.value)
    assert "NOSUCHPART" in message
    assert "parts list" in message, "the message must tell the user how to look for it"


def test_search_matches_name_and_family(tmp_path):
    lib = parts.Library(tmp_path / "library")
    lib.index = {
        "2N3904": {"family": "TRANSISTOR", "file": "a.xml"},
        "LM7805CT": {"family": "REGULATOR", "file": "b.xml"},
    }
    assert [n for n, _ in lib.search("3904")] == ["2N3904"]
    assert [n for n, _ in lib.search("regulator")] == ["LM7805CT"]
    assert len(list(lib.search(""))) == 2


def test_save_index_round_trips_and_refreshes_the_lookup(tmp_path):
    lib = parts.Library(tmp_path / "library")
    lib.index = {"R1": {"family": "RESISTOR", "file": "R1.xml"}}
    lib.save_index()
    again = parts.Library(tmp_path / "library")
    assert again.index["R1"]["family"] == "RESISTOR"
    assert again.resolve("r1") == "R1"


def test_safe_name_replaces_everything_a_filename_cannot_hold():
    assert parts.safe_name("2N3904") == "2N3904"
    assert parts.safe_name("LM7805CT") == "LM7805CT"
    assert parts.safe_name("a/b") == "a_b"
    assert parts.safe_name("a b") == "a_b"
    assert parts.safe_name("x*y") == "x_y"
    assert parts.safe_name("Q1.2+3-4") == "Q1.2+3-4"


def test_spice_pin_order_follows_the_template():
    template = "Q %tCollector %tBase %tEmitter 2N3904"
    assert parts.spice_pin_order(template, ["Emitter", "Base", "Collector"]) == ["Collector", "Base", "Emitter"]


def test_spice_pin_order_puts_unmentioned_pins_last():
    template = "Q %tBase %tEmitter 2N3904"
    assert parts.spice_pin_order(template, ["Base", "Emitter", "Substrate"]) == ["Base", "Emitter", "Substrate"]


def test_spice_pin_order_prefers_the_longest_matching_name():
    """A prefix of another pin name must not steal it."""
    template = "X %tSource %tSourceBias 1"
    assert parts.spice_pin_order(template, ["Source", "SourceBias"]) == ["Source", "SourceBias"]


def test_spice_pin_order_of_a_template_without_pins_is_the_input_order():
    assert parts.spice_pin_order("R 1 2 1k", ["A", "B"]) == ["A", "B"]


def test_db_lookup_finds_a_name_case_insensitively(tmp_path):
    path = tmp_path / "library"
    path.mkdir()
    (path / "db_index.json").write_text(json.dumps({"2N3904": ["Transistors", "BJT"]}), encoding="utf-8")
    lib = parts.Library(path)
    assert parts.db_lookup(lib, "2n3904") == ["Transistors", "BJT"]


def test_db_lookup_suggests_close_matches_when_the_name_is_missing(tmp_path):
    path = tmp_path / "library"
    path.mkdir()
    (path / "db_index.json").write_text(json.dumps({"LM7805CT": ["Power", "Regulator"]}), encoding="utf-8")
    lib = parts.Library(path)
    with pytest.raises(KeyError) as info:
        parts.db_lookup(lib, "7805")
    assert "LM7805CT" in str(info.value), "the error must list what does exist"


def test_build_db_index_reports_a_failed_node_run(tmp_path, monkeypatch):
    lib = parts.Library(tmp_path / "library")

    class Proc:
        returncode = 1
        stdout = ""
        stderr = "cannot open database"

    monkeypatch.setattr(parts.subprocess, "run", lambda *a, **k: Proc())
    with pytest.raises(RuntimeError, match="cannot open database"):
        parts.build_db_index(lib)

# --- identity() against real harvested parts ---------------------------------
# The library on this machine is used when it exists; the test skips otherwise so
# a fresh clone without a library still runs the rest.

@pytest.fixture(scope="module")
def real_library():
    lib = parts.Library()
    if not lib.index:
        pytest.skip("no part library has been harvested on this machine")
    return lib


def test_identity_of_a_transistor_is_name_and_family(real_library):
    part = real_library.load("2N3904")
    name, family, kind = parts.identity(part)
    assert name == "2N3904"
    assert family == "BJT_NPN"
    assert kind == "component"


def test_identity_of_a_scope_is_an_instrument(real_library):
    name, family, kind = parts.identity(real_library.load("SCOPE"))
    assert kind == "instrument"
    assert family == "INSTRUMENT"


def test_identity_of_ground_is_a_ground(real_library):
    name, family, kind = parts.identity(real_library.load("GROUND"))
    assert kind == "ground"
    assert name == "GROUND"


def test_describe_reports_pins_prefix_and_sections(real_library):
    info = parts.describe(real_library.load("2N3904"))
    assert info["pins"] == ["C", "B", "E"]
    assert info["prefix"] == "Q"
    assert info["sections"] == 1
    assert info["kind"] == "component"


def test_describe_records_the_spice_template(real_library):
    info = parts.describe(real_library.load("2N3904"))
    assert info["template"].startswith("q"), "a BJT template starts with q"
    assert "%tC" in info["template"]


def test_every_harvested_part_describes_without_raising(real_library):
    """A bad file in the library would otherwise only surface during a build."""
    for name in real_library.index:
        info = parts.describe(real_library.load(name))
        assert info["kind"] in ("component", "ground", "instrument"), name
        assert isinstance(info["pins"], list), name
        assert info["sections"] >= 1, name
        assert info["source"], name
