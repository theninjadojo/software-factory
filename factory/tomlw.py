"""A minimal TOML writer, enough for the overrides file the UI maintains (the standard library only reads TOML).
Supports nested tables, arrays of tables, and scalar/list values. Round-trips through tomllib (see tests)."""
import re

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")
_ESC = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r", "\b": "\\b", "\f": "\\f"}


def _str(s: str) -> str:
    """A TOML basic string in plain ASCII. Unlike JSON, characters beyond U+FFFF are one \\U escape, never a surrogate pair."""
    return '"' + "".join(_ESC.get(c) or (c if " " <= c < "\x7f" else f"\\u{ord(c):04x}" if ord(c) <= 0xFFFF else f"\\U{ord(c):08x}")
                         for c in s) + '"'


def _key(k: str) -> str:
    return k if _BARE.match(k) else _str(k)


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return _str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def _is_array_of_tables(v) -> bool:
    return isinstance(v, (list, tuple)) and len(v) > 0 and all(isinstance(x, dict) for x in v)


def dumps(d: dict, _prefix: str = "") -> str:
    out: list[str] = []
    for k, v in d.items():
        if not isinstance(v, dict) and not _is_array_of_tables(v):
            out.append(f"{_key(k)} = {_value(v)}")
    for k, v in d.items():
        if isinstance(v, dict):
            name = _prefix + _key(k)
            out.append(f"\n[{name}]")
            out.append(dumps(v, name + "."))
    for k, v in d.items():
        if _is_array_of_tables(v):
            name = _prefix + _key(k)
            for item in v:
                out.append(f"\n[[{name}]]")
                out.append(dumps(item, name + "."))
    return "\n".join(x for x in out if x != "").strip("\n") + "\n" if not _prefix else "\n".join(x for x in out if x != "")
