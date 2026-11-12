from urllib.parse import urlparse
import re

ALLOWED_SCHEMES = {"http", "https"}
MAX_REDIRECTS = 10

def validate_download_url(url: str) -> tuple[bool, str]:
    """Validate a user-supplied download URL without attempting a network request."""
    value = (url or "").strip()
    if not value:
        return False, "URL is empty."
    if any(ch.isspace() for ch in value):
        return False, "URL contains whitespace."
    try:
        p = urlparse(value)
    except ValueError as exc:
        return False, f"Invalid URL: {exc}"
    if p.scheme.lower() not in ALLOWED_SCHEMES:
        return False, "Only HTTP and HTTPS download URLs are supported."
    if not p.hostname:
        return False, "URL has no hostname."
    # Reject credentials embedded in the URL; they can leak through logs/history.
    if p.username is not None or p.password is not None:
        return False, "URLs containing embedded usernames/passwords are not allowed."
    if len(value) > 8192:
        return False, "URL is too long."
    return True, "OK"

def safe_filename(name: str) -> str:
    """Prevent path traversal and invalid Windows filename characters."""
    name = (name or "download").replace('\\', '_').replace('/', '_')
    name = re.sub(r'[\u202a-\u202e\u2066-\u2069]', '', name)          # bidi overrides can disguise the extension
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip().rstrip('.')
    if not name:
        name = "download"
    reserved = {"CON","PRN","AUX","NUL","CONIN$","CONOUT$",*(f"COM{i}" for i in range(1,10)),*(f"LPT{i}" for i in range(1,10))}
    stem = name.split('.',1)[0].rstrip().upper()          # Windows ignores trailing spaces: "con .txt" is still CON
    if stem in reserved:
        name = '_' + name
    return name[:240].rstrip('. ') or "download"

class DowngradeRefused(RuntimeError):
    """A redirect tried to move an HTTPS download onto plain HTTP."""


def refuse_https_downgrade(start_url: str):
    """httpx request hook: an HTTPS link must never be followed onto http://.

    Checked BEFORE each request is sent, so the plain-HTTP hop (URL, query string and headers in clear text)
    never leaves the machine. Plain-HTTP start URLs are unaffected: the user chose HTTP.
    """
    start_https = urlparse(start_url).scheme.lower() == "https"

    def hook(request):
        if start_https and request.url.scheme.lower() == "http":
            raise DowngradeRefused("Refused: the server redirected this HTTPS link to an unencrypted HTTP address.")
    return hook


def transport_label(url: str) -> str:
    return "HTTPS / TLS" if urlparse(url).scheme.lower() == "https" else "HTTP (unencrypted)"
