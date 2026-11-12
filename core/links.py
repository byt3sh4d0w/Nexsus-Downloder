"""Link identity helpers: duplicate detection and non-overwriting destinations."""
import re
from pathlib import Path
from urllib.parse import urlsplit

_UNRESERVED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _norm_escapes(s):
    """%7e -> ~ (unreserved is never encoded), other escapes get upper-case hex."""
    def fix(m):
        ch = chr(int(m.group(1), 16))
        return ch if ch in _UNRESERVED else "%" + m.group(1).upper()
    return re.sub(r"%([0-9a-fA-F]{2})", fix, s)


def normalize_url(url):
    """Canonical form used to compare links.

    Case of scheme/host, default ports, fragments and percent-escape spelling do not
    identify a resource. The path, the query string (order included: signed URLs carry
    tokens there) and any user:password stay exactly as given, so two differently
    signed links are never treated as the same link.
    """
    p = urlsplit(str(url or "").strip())
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    try:
        port = p.port
    except ValueError:
        port = None
    if port and port != {"http": 80, "https": 443}.get(scheme):
        host += f":{port}"
    user = ""
    if p.username is not None:
        user = p.username + (f":{p.password}" if p.password is not None else "") + "@"
    out = f"{scheme}://{user}{host}{_norm_escapes(p.path) or '/'}"
    if p.query:
        out += "?" + _norm_escapes(p.query)
    return out


def unique_path(path, taken=lambda p: False):
    """`name.ext` -> `name (1).ext`, `name (2).ext`... first one that is free on disk and not `taken`."""
    path = Path(path)
    def busy(p):
        return p.exists() or Path(str(p) + ".parts").exists() or Path(str(p) + ".part").exists() or taken(str(p))
    if not busy(path):
        return str(path)
    n = 1
    while busy(path.with_name(f"{path.stem} ({n}){path.suffix}")):
        n += 1
    return str(path.with_name(f"{path.stem} ({n}){path.suffix}"))


def magnet_info_hash(magnet):
    """40-hex btih of a magnet link (accepts base32 form), or ''. Used before touching the engine."""
    m = re.search(r"xt=urn:btih:([0-9a-zA-Z]+)", str(magnet), re.I)
    if not m:
        return ""
    h = m.group(1)
    if len(h) == 40 and re.fullmatch(r"[0-9a-fA-F]{40}", h):
        return h.lower()
    if len(h) == 32:
        import base64
        try:
            return base64.b32decode(h.upper()).hex()
        except Exception:
            return ""
    return ""
