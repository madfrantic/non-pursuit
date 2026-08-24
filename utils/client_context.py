"""Who the visitor is at the network layer, and how confident we are.

WHY THIS EXISTS SEPARATELY FROM THE VIEWS

Two screens describe the visitor's connection -- the dossier's "What Your
Connection Reveals Online" panel and the Results page's "Hello, <name>"
card -- and they had grown two different, both-wrong implementations:

* The dossier read `X-Client-Hostname` and `X-Timezone`. Neither header
  exists. No proxy in this stack sets them, so `headers.get(...)` always
  took its default and the panel rendered "Localhost / Loopback" and
  "Local Network / Subnet" as though they were measurements. It never
  raised, so it never looked broken -- it just quietly showed constants
  on every runtime, including the hosted one.
* The Results card called `ipinfo.io/json` with no address, which returns
  whatever IP *the server* egresses from. On the desktop build that is
  the user's own connection and the answer is right by accident; on the
  hosted build it is the datacenter, so the card confidently told a
  visitor in Queens they were in Virginia.

Both now come through `describe()`. The module is deliberately free of
any Streamlit import so it can be tested as plain functions.

WHAT "ACCURATE" MEANS HERE

Every field carries a status, and an unknown answer says so rather than
borrowing a plausible-looking default. `lookup()` separates the four ways
the geolocation call fails, because they need different words on screen
and only one of them is worth retrying:

  ok           a real answer
  private      a LAN/loopback address; ipinfo answers 200 {"bogon": true}
               and no registry can place it, which is not an error
  rate_limited HTTP 429 -- the free tier is ~1k lookups/day, and an
               uncached call on every Streamlit rerun burns that in an
               afternoon. Callers must cache; see master.py's TTL wrapper
  unreachable  timeout, DNS failure, 5xx
  malformed    HTTP 200 whose body is not the JSON object we expect

The old code collapsed all four into one "Unavailable" string and logged
only the exception cases -- a 429 returned silently.
"""
import ipaddress

import requests

from applog import get_logger

_log = get_logger("client_context")

IPINFO_URL = "https://ipinfo.io/{ip}/json"
DEFAULT_TIMEOUT = 4

# Order matters: the first header a fronting proxy sets wins. X-Forwarded-For
# is the standard one Streamlit Community Cloud and nginx populate; X-Real-Ip
# is nginx's single-value form; the CF/Fly headers cost nothing to check and
# are unambiguous when present.
_CLIENT_IP_HEADERS = (
    "X-Forwarded-For",
    "X-Real-Ip",
    "Cf-Connecting-Ip",
    "Fly-Client-Ip",
)

UNKNOWN = "Unknown"


def _normalise_ip(raw):
    """A single header value -> a valid IP string, or None.

    Handles the shapes that turn up in a forwarded chain: a bare address,
    an address with a port, and a bracketed IPv6 literal with or without
    one. Anything that does not parse as an address is discarded rather
    than displayed -- these headers are attacker-settable and end up on
    screen.
    """
    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith("["):  # [2001:db8::1]:443
        value = value[1:].split("]", 1)[0]
    elif value.count(":") == 1:  # 203.0.113.7:41234 -- never bare IPv6
        value = value.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def client_ip(headers):
    """The visitor's address as reported by the proxy in front of us.

    X-Forwarded-For is a chain, client first, one hop appended per proxy
    ("203.0.113.7, 10.0.0.4"). The old dossier printed the whole string;
    the leftmost entry is the one that means anything to a viewer.

    Returns None when no proxy header is present, which is the normal
    case for a desktop install -- there the browser and the server are
    the same machine and there is no forwarded address to read.
    """
    if not headers:
        return None
    for name in _CLIENT_IP_HEADERS:
        raw = headers.get(name) or headers.get(name.lower())
        if not raw:
            continue
        for hop in str(raw).split(","):
            found = _normalise_ip(hop)
            if found:
                return found
    return None


def is_public(ip):
    """False for loopback, RFC1918, link-local, CGNAT and friends."""
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def _empty(status, ip=None):
    return {
        "status": status,
        "ip": ip or UNKNOWN,
        "hostname": None,
        "city": None,
        "region": None,
        "country": None,
        "org": None,
        "timezone": None,
    }


def lookup(ip=None, timeout=DEFAULT_TIMEOUT, session=None):
    """Geolocate one address through ipinfo.io.

    `ip=None` looks up whatever address this process egresses from, which
    is only the visitor on a single-user desktop install. Server-side code
    serving other people must pass the address from `client_ip()`.

    Never raises: every failure comes back as a status the caller can put
    on screen.
    """
    if ip is not None and not is_public(ip):
        return _empty("private", ip)

    getter = (session or requests).get
    try:
        response = getter(IPINFO_URL.format(ip=ip or ""), timeout=timeout)
    except Exception:
        _log.exception("ipinfo.io lookup failed for %s", ip or "self")
        return _empty("unreachable", ip)

    status_code = getattr(response, "status_code", None)
    if status_code == 429:
        # Worth its own status: the data is fine, we are simply out of
        # quota, and telling a presenter "rate limited" points at the fix
        # (a token, or a longer cache TTL) where "Unavailable" does not.
        _log.warning("ipinfo.io rate limited (429) for %s", ip or "self")
        return _empty("rate_limited", ip)
    if not getattr(response, "ok", False):
        _log.warning("ipinfo.io returned HTTP %s for %s", status_code, ip or "self")
        return _empty("unreachable", ip)

    try:
        payload = response.json()
    except Exception:
        _log.exception("ipinfo.io returned a non-JSON body for %s", ip or "self")
        return _empty("malformed", ip)
    if not isinstance(payload, dict):
        _log.warning("ipinfo.io returned %s, not an object", type(payload).__name__)
        return _empty("malformed", ip)
    if payload.get("bogon"):
        return _empty("private", payload.get("ip") or ip)
    if not payload.get("ip"):
        # A 200 with no address at all is the shape an error page takes
        # when it is served as JSON; treat it as malformed rather than
        # rendering a card full of Nones.
        _log.warning("ipinfo.io payload carried no ip field for %s", ip or "self")
        return _empty("malformed", ip)

    return {
        "status": "ok",
        "ip": payload.get("ip") or ip or UNKNOWN,
        "hostname": payload.get("hostname"),
        "city": payload.get("city"),
        "region": payload.get("region"),
        "country": payload.get("country"),
        "org": payload.get("org"),
        "timezone": payload.get("timezone"),
    }


def location_label(info):
    """"New York City, New York, US" from whatever fields came back."""
    parts = [info.get("city"), info.get("region"), info.get("country")]
    return ", ".join(part for part in parts if part)


def describe(headers, lookup_fn=lookup):
    """The full picture for one request: headers plus geolocation.

    `lookup_fn` is injected so the views can pass a cached wrapper -- see
    the rate-limit note in the module docstring -- and so tests can run
    without a network.
    """
    ip = client_ip(headers)
    if ip is None:
        # No proxy header: local install, or a hosted one whose front end
        # strips them. Either way there is no address to geolocate, and
        # asking ipinfo would return the server's own.
        info = _empty("no_proxy_header")
    else:
        info = lookup_fn(ip)
    info = dict(info)
    info["client_ip"] = ip
    info["location"] = location_label(info)
    return info


# --- device, from client hints then the User-Agent -------------------
#
# Chromium sends Sec-CH-UA-Platform by default (it is a low-entropy hint,
# no Accept-CH negotiation needed) and it is the only field here that is
# not a guess parsed out of a marketing string. It arrives quoted:
#   Sec-CH-UA-Platform: "Linux"
# Firefox and Safari send no hints at all, so the User-Agent stays the
# fallback rather than the primary.
_PLATFORM_HINT_HEADERS = ("Sec-CH-UA-Platform", "Sec-Ch-Ua-Platform")

# Hint value -> what we display. The hint's vocabulary is fixed by the
# spec, so this is a lookup and not a heuristic.
_PLATFORM_HINTS = {
    "linux": "Linux",
    "macos": "macOS",
    "windows": "Windows",
    "android": "Android",
    "ios": "iOS",
    "chrome os": "Chrome OS",
    "chromium os": "Chrome OS",
}

# Substring -> OS, in priority order. Order is the whole point:
# "X11; Ubuntu; Linux x86_64" contains both Ubuntu and Linux, and
# "Linux; Android 14" contains both Android and Linux, so the specific
# name has to win over the kernel it runs on.
_UA_PLATFORMS = (
    ("Android", "Android"),
    ("Ubuntu", "Ubuntu"),
    ("CrOS", "Chrome OS"),
    ("iPhone", "iOS"),
    ("iPad", "iPadOS"),
    ("Macintosh", "macOS"),
    ("Mac OS X", "macOS"),
    ("Windows", "Windows"),
    ("Linux", "Linux"),
    ("X11", "Linux"),
)

# Engine, likewise ordered: every Chrome UA also says "Safari", and
# Edge's says Chrome and Safari as well as "Edg".
_UA_ENGINES = (
    ("Firefox", "Gecko"),
    ("Edg/", "Blink / V8"),
    ("OPR/", "Blink / V8"),
    ("Chromium", "Blink / V8"),
    ("Chrome", "Blink / V8"),
    ("Safari", "WebKit"),
)


def platform_hint(headers):
    """`Sec-CH-UA-Platform` as a display string, or None."""
    if not headers:
        return None
    for name in _PLATFORM_HINT_HEADERS:
        raw = headers.get(name) or headers.get(name.lower())
        if raw:
            return _PLATFORM_HINTS.get(str(raw).strip().strip('"').lower())
    return None


def os_family(headers=None, user_agent=""):
    """The visitor's OS: client hint first, User-Agent second.

    A Linux desktop reported as macOS is the specific failure this
    replaces -- the panel used to test `"Mac" in ua`, which matches the
    "Macintosh" in a UA but also any string containing "Mac", and had no
    Ubuntu, Android or Chrome OS case at all.
    """
    hinted = platform_hint(headers)
    ua = user_agent or ""
    if hinted:
        # Ubuntu is the one thing the hint cannot say -- it reports the
        # kernel, not the distribution -- so let the UA sharpen it.
        if hinted == "Linux" and "Ubuntu" in ua:
            return "Ubuntu"
        return hinted
    for needle, label in _UA_PLATFORMS:
        if needle in ua:
            return label
    return "Unknown OS"


def browser_engine(user_agent=""):
    """Rendering engine from the User-Agent, or "Unknown Engine"."""
    ua = user_agent or ""
    for needle, label in _UA_ENGINES:
        if needle in ua:
            return label
    return "Unknown Engine"


def device_profile(headers=None):
    """(user-agent display string, OS, engine) for one request."""
    headers = headers or {}
    ua = headers.get("User-Agent") or headers.get("user-agent") or ""
    display = ua[:60] if ua else "Not disclosed"
    return (display, os_family(headers, ua), browser_engine(ua))
