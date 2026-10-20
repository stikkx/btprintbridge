"""vCard preset: a contact QR code (name, email, phone) for phone cameras.

``payload()`` builds a compact vCard 3.0 (only N, FN, EMAIL, TEL; CRLF line
ends, RFC 2426 escaping). Kept small on purpose: every byte makes the QR
code denser, and a 12 mm tape has only 76 dots for it.
"""

import re

FIELDS = ("name", "email", "phone")
MAX_LEN = 100
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+$")
_PHONE_RE = re.compile(r"^\+?[0-9 ()/.\-]+$")


def _escape(value: str) -> str:
    return (value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;"))


def validate(card) -> dict:
    """Checked ``{name, email, phone}`` (stripped strings, '' when absent)."""
    if not isinstance(card, dict):
        raise ValueError("'vcard' must be an object with name, email, phone")
    unknown = set(card) - set(FIELDS)
    if unknown:
        raise ValueError(f"unknown vcard field(s): {', '.join(sorted(unknown))}")
    out = {}
    for key in FIELDS:
        value = card.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError(f"vcard {key} must be a string")
        value = value.strip()
        if len(value) > MAX_LEN or any(ord(c) < 32 for c in value):
            raise ValueError(f"vcard {key} must be one line of at most {MAX_LEN} characters")
        out[key] = value
    if not any(out.values()):
        raise ValueError("vcard needs at least one of name, email, phone")
    if out["email"] and not _EMAIL_RE.match(out["email"]):
        raise ValueError(f"vcard email is not an email address: {out['email']}")
    if out["phone"] and not (_PHONE_RE.match(out["phone"]) and re.search(r"\d", out["phone"])):
        raise ValueError(f"vcard phone may only contain digits, +, spaces, ( ) / . -: {out['phone']}")
    return out


def payloads(card: dict) -> list[str]:
    """Payload variants for *card*, most complete first: CRLF with N + FN,
    LF with N + FN, LF with N only. Readers accept all three; the renderer
    takes the first whose QR still gets 2-dot modules on the tape."""
    full = payload(card["name"], card["email"], card["phone"])
    lf = full.replace("\r\n", "\n")
    if card["name"]:
        compact = "\n".join(l for l in lf.split("\n") if not l.startswith("FN:"))
    else:
        compact = lf          # without a name FN is the only display name
    return [full, lf, compact]


def payload(name: str, email: str, phone: str) -> str:
    """vCard 3.0 text for the QR code."""
    given, _, family = name.rpartition(" ")
    if not given:                       # one word: treat it as the family name
        given, family = "", family
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"N:{_escape(family)};{_escape(given)}",
        f"FN:{_escape(name or email or phone)}",
    ]
    if email:
        lines.append(f"EMAIL:{_escape(email)}")
    if phone:
        lines.append("TEL:" + re.sub(r"[^\d+]", "", phone))
    lines.append("END:VCARD")
    return "\r\n".join(lines)
