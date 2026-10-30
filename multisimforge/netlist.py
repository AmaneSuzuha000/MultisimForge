"""Parse a circuit netlist (SPICE syntax plus a few extensions) into the part instances to place.

Supported lines:
    R/C/L   R1 a b 1k                       resistor / capacitor / inductor
    V       V1 a 0 DC 12 | DC 0 AC 1 SIN(0 1 1k) | PULSE(0 5 0 1u 1u 1m 2m)
    I       I1 a 0 DC 1m
    D/Q/M/J D1 a k 1N4148    Q1 c b e 2N3904    M1 d g s b 2N7000
    X/U     XU1 n1 n2 ... LM324AJ           in 'parts show' pin order
            U1 LM555CN GND=0 TRI=n1 ...     connected by pin name (clearer for chips with many pins)
    instrument  XSC1 A+ B+ A- T+ B- T- OSCILLOSCOPE
            XFG1 + COM - FGEN WAVE=SQUARE FREQ=1k AMPLITUDE=2 OFFSET=0
            XBP1 IN+ IN- OUT+ OUT- BODE
            XMM1 + - MULTIMETER
Comments start with *, .end and other dot commands are ignored; a line starting with + continues the previous one.
Net names are case-insensitive; 0 and gnd are both ground.
"""
import re
from dataclasses import dataclass, field

SUFFIX = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "": 1.0, "m": 1e-3, "u": 1e-6, "µ": 1e-6,
          "n": 1e-9, "p": 1e-12, "f": 1e-15}
_MULT = r"meg|[tgkmuµnpf]"
# Unit words a value may carry without changing it (1kohm, 10mV, 5Hz). Anything else is a typo.
_UNITS = {"", "ohm", "ohms", "f", "farad", "farads", "h", "henry", "henries", "v", "volt", "volts",
          "a", "amp", "amps", "ampere", "amperes", "w", "watt", "watts", "hz", "s", "sec", "secs",
          "second", "seconds"}
_NUM = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(" + _MULT + r")?\s*([a-z]*)$", re.I)
# Engineering notation where the multiplier replaces the decimal point: 1k2 = 1.2k, 4u7 = 4.7u.
_RKM = re.compile(r"^([+-]?\d+)(" + _MULT + r")(\d+)\s*([a-z]*)$", re.I)

# Instruments: netlist keyword -> part library name, positional pin order (port names)
INSTRUMENTS = {
    "XSC": ({"OSCILLOSCOPE", "SCOPE", "OSC"}, "SCOPE"),
    "XFG": ({"FGEN", "FUNCTION_GENERATOR", "XFG"}, "FGEN"),
    "XBP": ({"BODE", "BODE_PLOTTER", "XBP"}, "BODE"),
    "XMM": ({"MULTIMETER", "MULMETER", "DMM"}, "MULMETER"),
}
NODE_COUNT = {"R": 2, "C": 2, "L": 2, "V": 2, "I": 2, "D": 2, "Q": 3, "J": 3, "M": 4}


def parse_value(token) -> float:
    """Value: 4.7k, 100n, 1meg, 10mV, 1k2 (the whole token must be a value).

     Case-sensitive like the Multisim UI: M is mega (1MHz, 1MOhm), m is milli; meg is mega in any case.
     A trailing unit word is allowed (1kohm, 10mV, 5Hz); any other trailing text is an error, so a
     typo such as 1kgarbage or 4.7kjunk is rejected instead of silently becoming 1k.
    """
    if isinstance(token, (int, float)):
        return float(token)
    text = str(token).strip()
    m = _RKM.match(text)
    if m:
        whole, mult, frac, unit = m.groups()
        if unit.lower() not in _UNITS:
            raise ValueError(f"cannot parse value {token!r}")
        scale = 1e6 if mult == "M" else SUFFIX[mult.lower()]
        return float(f"{whole}.{frac}") * scale
    m = _NUM.match(text)
    if not m:
        raise ValueError(f"cannot parse value {token!r}")
    number, mult, unit = m.group(1), m.group(2) or "", m.group(3)
    if unit.lower() not in _UNITS:
        raise ValueError(f"cannot parse value {token!r}")
    return float(number) * (1e6 if mult == "M" else SUFFIX[mult.lower()])


def net_name(token: str) -> str:
    t = token.strip()
    return "0" if t.lower() in ("0", "gnd") else t.lower()


@dataclass
class Instance:
    refdes: str
    part: str                       # part library name
    nets: list = field(default_factory=list)       # positional form
    named: dict = field(default_factory=dict)      # pin-name form {pin: net}
    value: str | None = None        # R/C/L value
    source: dict | None = None      # source parameters {"type": "dc"/"sin"/"pulse", ...}
    params: dict = field(default_factory=dict)     # instrument parameters such as WAVE/FREQ for XFG
    line: str = ""


def _logical_lines(text: str):
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith(("*", ";")):
            continue
        if s.startswith("+") and lines:
            lines[-1] += " " + s[1:].strip()
        else:
            lines.append(s)
    return lines


def _source(rest: str, refdes: str) -> dict:
    """Parse the DC / AC / SIN / PULSE part of a voltage or current source."""
    text = rest.strip()
    out = {"type": "dc", "dc": 0.0, "ac": None}
    m = re.search(r"(?i)\bsin\s*\(([^)]*)\)", text)
    if m:
        args = [parse_value(a) for a in m.group(1).replace(",", " ").split()]
        if len(args) < 3:
            raise ValueError(f"{refdes}: SIN needs at least (offset peak frequency)")
        out.update(type="sin", offset=args[0], amplitude=args[1], freq=args[2],
                   delay=args[3] if len(args) > 3 else 0.0, damping=args[4] if len(args) > 4 else 0.0,
                   phase=args[5] if len(args) > 5 else 0.0)
        text = text[:m.start()] + text[m.end():]
    m = re.search(r"(?i)\bpulse\s*\(([^)]*)\)", text)
    if m:
        args = [parse_value(a) for a in m.group(1).replace(",", " ").split()]
        if len(args) != 7:
            raise ValueError(f"{refdes}: PULSE needs 7 parameters (low high delay rise fall width period)")
        out.update(type="pulse", pulse=args)
        text = text[:m.start()] + text[m.end():]
    m = re.search(r"(?i)\bac\s+(\S+)(?:\s+([+-]?[\d.]+))?", text)
    if m:
        out["ac"] = (parse_value(m.group(1)), float(m.group(2)) if m.group(2) else 0.0)
        text = text[:m.start()] + text[m.end():]
    m = re.search(r"(?i)\bdc\s+(\S+)", text)
    if m:
        out["dc"] = parse_value(m.group(1))
        text = text[:m.start()] + text[m.end():]
    leftover = text.split()
    if leftover and out["type"] == "dc" and len(leftover) == 1:
        out["dc"] = parse_value(leftover[0])      # V1 a 0 12
    elif leftover:
        raise ValueError(f"{refdes}: unrecognised source parameter {' '.join(leftover)!r}")
    return out


def parse(text: str) -> list[Instance]:
    out, seen = [], set()
    for line in _logical_lines(text):
        if line.startswith("."):
            continue
        tokens = line.split()
        ref = tokens[0]
        key = ref.upper()
        if key in seen:
            raise ValueError(f"duplicate refdes: {ref}")
        seen.add(key)
        inst = Instance(refdes=ref, part="", line=line)
        prefix3 = key[:3]
        if prefix3 in INSTRUMENTS:
            keywords, part = INSTRUMENTS[prefix3]
            idx = next((i for i, t in enumerate(tokens) if t.upper() in keywords), None)
            if idx is None:
                raise ValueError(f"{ref}: an instrument line must end with {sorted(keywords)[0]}, e.g. {line} {sorted(keywords)[0]}")
            inst.part = part
            inst.nets = [net_name(t) for t in tokens[1:idx]]
            for t in tokens[idx + 1:]:
                k, _, v = t.partition("=")
                inst.params[k.upper()] = v
        elif key[0] in "RCL" and not key.startswith("LED"):
            if len(tokens) < 4:
                raise ValueError(f"{ref}: write {ref} node1 node2 value")
            inst.part = {"R": "RESISTOR_VIRTUAL", "C": "CAPACITOR_VIRTUAL", "L": "INDUCTOR_VIRTUAL"}[key[0]]
            inst.nets = [net_name(t) for t in tokens[1:3]]
            inst.value = tokens[3]
            parse_value(inst.value)
            if len(tokens) > 4:
                inst.part = tokens[4]          # explicit library part, e.g. an electrolytic capacitor
        elif key[0] in "VI":
            inst.nets = [net_name(t) for t in tokens[1:3]]
            inst.source = _source(" ".join(tokens[3:]), ref)
            kind = inst.source["type"]
            if key[0] == "V":
                inst.part = {"dc": "DC_POWER", "sin": "AC_VOLTAGE", "pulse": "PULSE_VOLTAGE"}[kind]
            else:
                if kind != "dc":
                    raise ValueError(f"{ref}: only DC current sources are supported")
                inst.part = "DC_CURRENT"
        elif key[0] in "DQJM":
            n = NODE_COUNT[key[0]]
            if len(tokens) != n + 2:
                raise ValueError(f"{ref}: write {ref} {' '.join(['node'] * n)} model")
            inst.nets = [net_name(t) for t in tokens[1:n + 1]]
            inst.part = tokens[n + 1]
        elif key[0] in "XU" or key.startswith("LED"):
            body = tokens[1:]
            if any("=" in t for t in body):
                names = [t for t in body if "=" not in t]
                if len(names) != 1:
                    raise ValueError(f"{ref}: pin-name form is {ref} model pin=net ...")
                inst.part = names[0]
                for t in body:
                    if "=" in t:
                        pin, _, net = t.partition("=")
                        inst.named[pin] = net_name(net)
            else:
                if len(body) < 2:
                    raise ValueError(f"{ref}: write {ref} node... model")
                inst.part = body[-1]
                inst.nets = [net_name(t) for t in body[:-1]]
        else:
            raise ValueError(f"unsupported part line: {line}")
        out.append(inst)
    return out
