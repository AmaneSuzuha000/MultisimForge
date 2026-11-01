"""Small, platform-independent policy boundary for the local MCP adapter."""
from pathlib import Path


READ_ONLY_COMMANDS = frozenset({"check", "inspect", "cursors"})


def safe_escape_command(argv: list[str]) -> str:
    """Return a reason if run_cli cannot execute a read-only command.

    Parse the supported global --debug prefix before inspecting the command;
    a simple argv[0] blacklist was bypassable by ['--debug', 'com', 'stop'].
    """
    args = list(argv)
    if args and args[0] == "--debug":
        args.pop(0)
    if not args:
        return "no subcommand given"
    if args[0] not in READ_ONLY_COMMANDS:
        return f"run_cli permits only {', '.join(sorted(READ_ONLY_COMMANDS))}; use typed tools for other operations"
    # A read-only command cannot inject a second subcommand through the CLI parser.
    return ""


def require_write_target(path: Path, root: Path) -> Path:
    """Return resolved destination or refuse writes outside the configured sandbox."""
    dest = Path(path).resolve()
    root = Path(root).resolve()
    if dest != root and root not in dest.parents:
        raise PermissionError(f"MCP writes are confined to {root}; set MULTISIMFORGE_MCP_OUTPUT_ROOT to widen intentionally")
    return dest
