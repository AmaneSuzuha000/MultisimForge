import pytest

from multisimforge.access import require_write_target, safe_escape_command


@pytest.mark.parametrize("args", [
    ["com", "stop"], ["--debug", "com", "stop"], ["--debug", "--debug", "com", "stop"],
    ["parts", "init"], ["--debug", "parts", "harvest"],
    ["set-sim-time", "original.ms14", "20ms"],
    ["decode", "x.ms14", "outside.xml"],
    ["sim", "a.ms14", "--csv", "/tmp/out"],
    ["build", "spec.json"], [],
])
def test_mcp_escape_hatch_cannot_mutate_or_launch(args):
    assert safe_escape_command(args)


@pytest.mark.parametrize("args", [
    ["check", "x.ms14"],
    ["inspect", "x.ms14"],
    ["cursors", "tran.csv", "--column", "vout", "--x1", "1m", "--x2", "2m"],
    ["--debug", "check", "x.ms14"],
    ["--debug", "--debug", "check", "x.ms14"],
])
def test_mcp_escape_hatch_read_only(args):
    assert safe_escape_command(args) == ""


def test_write_root_blocks_absolute_and_relative_escape(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    assert require_write_target(root / "out" / "x.ms14", root) == (root / "out" / "x.ms14").resolve()
    for dst in (tmp_path / "original.ms14", root / ".." / "original.ms14"):
        with pytest.raises(PermissionError, match="confined"):
            require_write_target(dst, root)


def test_write_root_blocks_symlink_escape(tmp_path):
    root, external = tmp_path / "work", tmp_path / "elsewhere"
    root.mkdir()
    external.mkdir()
    try:
        (root / "link").symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(PermissionError):
        require_write_target(root / "link" / "output.ms14", root)
