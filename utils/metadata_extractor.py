"""
Pull identity signal out of a profile page body.

The account-existence scanners this borrows from stop at a boolean. That is
the right answer to "does this handle exist here", and the wrong answer to
the question a compliance workflow actually has: "is this the same person?"
A handle existing on 40 platforms says nothing; a display name, a city and
an outbound link that points at another one of those 40 says a great deal.

Two kinds of extraction happen here, and they are not equally reliable:

  * Attributes (name, bio, location, avatar) come from OpenGraph and
    standard meta tags, plus a shallow probe of JSON API responses. These
    are advisory -- an og:title is frequently the site name, not the person.
  * Outbound links are the load-bearing part. A link found on a profile
    page, resolved back to another known platform and handle, is the single
    strongest corroboration signal available without authenticating, because
    the account holder had to put it there deliberately.

Deliberately regex-based rather than an HTML parser. The bodies here are
arbitrary, frequently malformed, sometimes 5 MB, and fetched from ~3000
sites of wildly varying quality; a strict parser turns a malformed page into
an exception on a path where the correct behaviour is "extract what you can
and move on". Bodies are truncated before scanning for the same reason -- the
metadata worth having lives in <head>, and scanning megabytes of comment
threads for it is pure cost.

Nothing here follows a link. Extraction is read-only over a response this
process already fetched for the existence check.
"""
import html
import json
import re

# Only the first slice of a body is scanned for meta tags. og:* and <title>
# are in <head> by definition; anything claiming to be one 200 KB into the
# document is page content, not metadata.
HEAD_SCAN_BYTES = 200_000
# Links are scanned over more of the body -- a "my links" block often sits
# well down the page -- but still bounded.
LINK_SCAN_BYTES = 400_000

# Markers identifying a meta tag, matched against the whole tag so that
# attribute order doesn't matter -- both `<meta property="og:title"
# content="...">` and `<meta content="..." property="og:title">` occur in
# the wild, and a pattern that hardcodes one order silently misses half.
_META_MARKERS = {
    "name": ("og:title", "twitter:title"),
    "bio": ("og:description", "twitter:description", 'name="description"',
            "name='description'"),
    "avatar": ("og:image", "twitter:image"),
    "location": ("og:locality", "profile:location", "geo.placename"),
}

_META_TAG_RE = re.compile(r"<meta\s[^>]{0,600}?>", re.IGNORECASE)
_CONTENT_RE = re.compile(r'content=["\']([^"\']{1,500})["\']', re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>([^<]{1,200})</title>", re.IGNORECASE)

# JSON keys checked when a response body parses as an object. Ordered by how
# specific the key is: a "full_name" is a person, a "name" might be anything.
_JSON_KEYS = {
    "name": ("full_name", "display_name", "displayName", "realname", "name"),
    "bio": ("bio", "description", "about", "summary", "headline"),
    "location": ("location", "city", "region", "地区"),
    "avatar": ("avatar_url", "avatarUrl", "profile_image_url", "avatar", "image"),
    "email": ("email", "public_email"),
}

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,24}"
)
# Addresses that appear on nearly every page and belong to the platform, not
# the person. Matching on the local part catches the whole family without
# needing to enumerate domains.
_EMAIL_NOISE = re.compile(
    r"^(?:no-?reply|do-?not-?reply|support|info|help|admin|abuse|postmaster|"
    r"webmaster|contact|hello|sales|press|legal|privacy|security|team)@",
    re.IGNORECASE,
)
_HREF_RE = re.compile(r'href=["\'](https?://[^"\'\s>]{5,300})["\']', re.IGNORECASE)
_JSON_URL_RE = re.compile(r'"(?:url|website|blog|link|homepage)"\s*:\s*"(https?://[^"]{5,300})"')

# Locations are only read from explicit markup. Guessing a city out of free
# text produces confident nonsense ("Reading" is a verb and a town), and a
# wrong location propagates straight into a demand letter's identity block.
_LOCATION_PATTERNS = (
    r'<meta[^>]+(?:property|name)=["\'](?:og:locality|profile:location|geo\.placename)["\'][^>]+content=["\']([^"\']{2,100})["\']',
    r'itemprop=["\']address(?:Locality)?["\'][^>]*>\s*([^<]{2,100})<',
    r'class=["\'][^"\']*(?:profile-)?location[^"\']*["\'][^>]*>\s*([^<]{2,100})<',
)


def _clean(value: str) -> str:
    value = html.unescape(value or "")
    return re.sub(r"\s+", " ", value).strip()


def _meta_content(head: str, markers) -> str:
    """Content of the first meta tag whose text contains one of *markers*."""
    for marker in markers:
        for tag in _META_TAG_RE.findall(head):
            if marker.lower() not in tag.lower():
                continue
            match = _CONTENT_RE.search(tag)
            if match:
                cleaned = _clean(match.group(1))
                if cleaned:
                    return cleaned
    return ""


def _first_match(text: str, patterns) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            cleaned = _clean(match.group(1))
            if cleaned:
                return cleaned
    return ""


def _walk_json(node, depth: int = 0):
    """Yield every dict in a parsed JSON body, up to a bounded depth.

    API responses wrap the person one or two levels down ({"data": {"user":
    {...}}}) often enough that reading only the top level misses most of
    them, and deep enough that unbounded recursion on a hostile body is a
    real cost.
    """
    if depth > 4:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_json(value, depth + 1)
    elif isinstance(node, list):
        for item in node[:20]:
            yield from _walk_json(item, depth + 1)


def extract_from_json(body: str) -> dict:
    """Attributes from a JSON API response, or {} if the body isn't JSON.

    Many of the highest-value checks in the registry hit an API endpoint
    rather than an HTML page (Reddit's about.json, GitHub's users API), and
    those bodies carry cleaner identity fields than any profile page.
    """
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return {}

    found = {}
    for node in _walk_json(payload):
        for field, keys in _JSON_KEYS.items():
            if field in found:
                continue
            for key in keys:
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    found[field] = _clean(value)[:500]
                    break
    return found


def extract_emails(body: str) -> list:
    """Plausible personal addresses in the body, platform noise removed."""
    seen, keep = set(), []
    for candidate in _EMAIL_RE.findall(body[:LINK_SCAN_BYTES]):
        lowered = candidate.lower()
        if lowered in seen or _EMAIL_NOISE.match(lowered):
            continue
        # Bare image and asset filenames regularly match the address shape
        # (sprite@2x.png style paths get close enough).
        if lowered.rsplit(".", 1)[-1] in {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js"}:
            continue
        seen.add(lowered)
        keep.append(candidate)
    return keep[:10]


def _same_host(url: str, host: str) -> bool:
    """True when *url* points at *host* or one of its subdomains.

    Suffix matching on a dot boundary, not a substring test: "github.com"
    must not match "notgithub.com", and must still match
    "gist.github.com".
    """
    try:
        netloc = url.split("://", 1)[1].split("/", 1)[0].split("@")[-1].split(":")[0]
    except IndexError:
        return False
    host = host.lower().lstrip(".")
    return netloc == host or netloc.endswith("." + host)


def extract_links(body: str, self_host: str = "") -> list:
    """Outbound absolute URLs from the body, deduplicated, self-host removed.

    Links back to the host being scanned are dropped: a Twitter page linking
    to twitter.com is navigation, not corroboration.
    """
    seen, keep = set(), []
    window = body[:LINK_SCAN_BYTES]
    for url in _HREF_RE.findall(window) + _JSON_URL_RE.findall(window):
        url = html.unescape(url).rstrip("/")
        lowered = url.lower()
        if lowered in seen:
            continue
        if self_host and _same_host(lowered, self_host):
            continue
        seen.add(lowered)
        keep.append(url)
        if len(keep) >= 200:
            break
    return keep


def extract(body: str, *, content_type: str = "", self_host: str = "") -> dict:
    """Everything extractable from one response body.

    Returns a flat dict with the keys the identity graph consumes:
    name, bio, location, avatar, emails, links. Missing fields are "" or
    [] rather than absent, so callers never branch on presence.
    """
    body = body or ""
    head = body[:HEAD_SCAN_BYTES]

    found = {"name": "", "bio": "", "location": "", "avatar": "", "email": ""}
    if "json" in content_type.lower() or body.lstrip()[:1] in "{[":
        found.update({k: v for k, v in extract_from_json(body).items() if v})

    for field, markers in _META_MARKERS.items():
        if not found.get(field):
            found[field] = _meta_content(head, markers)
    if not found.get("name"):
        found["name"] = _first_match(head, (_TITLE_RE.pattern,))
    if not found.get("location"):
        found["location"] = _first_match(head, _LOCATION_PATTERNS)

    emails = extract_emails(body)
    if found.get("email") and found["email"] not in emails:
        emails.insert(0, found["email"])
    found.pop("email", None)

    found["emails"] = emails
    found["links"] = extract_links(body, self_host=self_host)
    return found


# --------------------------------------------------------------------------
# Reverse link resolution
# --------------------------------------------------------------------------

# A path segment that is a site's own furniture, not somebody's handle. A
# link to github.com/settings resolved as the handle "settings" would let
# every scanned page corroborate the same fictitious identity.
_RESERVED_HANDLES = frozenset({
    "about", "account", "admin", "api", "auth", "blog", "contact", "developer",
    "developers", "docs", "download", "explore", "faq", "features", "help",
    "home", "join", "legal", "login", "logout", "new", "news", "notifications",
    "pricing", "privacy", "pro", "register", "search", "security", "settings",
    "signin", "signup", "sitemap", "status", "support", "terms", "tos",
    "trending", "u", "user", "users", "welcome", "www",
})


def _template_to_regex(template: str) -> re.Pattern | None:
    """Turn a uri_check template into a matcher that recovers the handle.

    The scheme and any leading www. are stripped from the template and
    re-added as an optional prefix, because the same profile gets linked all
    four ways (http/https x with/without www) and a matcher pinned to the
    dataset's spelling recognises roughly a quarter of the links it should.

    The remainder is regex-escaped before the placeholder is swapped in, so
    a template's dots and query strings are matched literally rather than
    acting as metacharacters -- without that, ``https://x.com/{account}``
    would happily match ``https://xqcom/anything``.
    """
    if "{account}" not in template:
        return None

    body = re.sub(r"^https?://", "", template.strip(), flags=re.IGNORECASE)
    body = re.sub(r"^www\.", "", body, flags=re.IGNORECASE)

    head, _, tail = body.partition("{account}")
    if "{account}" in tail:  # more than one placeholder: not resolvable
        return None

    pattern = (
        r"https?://(?:www\.)?"
        + re.escape(head)
        + r"(?P<handle>[A-Za-z0-9_.\-]{2,64})"
        + re.escape(tail)
        + r"/?$"
    )
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


def build_link_resolver(sites: list) -> list:
    """Compile (platform, pattern) pairs from a registry site list.

    Built once per scan and reused across every response. uri_pretty is
    preferred where it exists -- that is the URL a human would actually
    paste, and therefore the one that shows up in other people's profiles.
    """
    resolvers = []
    for site in sites:
        for template in (site.get("uri_pretty"), site.get("uri_check")):
            if not template:
                continue
            pattern = _template_to_regex(template)
            if pattern:
                resolvers.append((site.get("name", ""), pattern))
                break
    return resolvers


def resolve_link(url: str, resolvers: list) -> tuple[str, str] | None:
    """Map an outbound URL back to (platform, handle), or None.

    None is the common and correct answer -- most links on a profile page
    go to news articles and project pages, not to other profiles.
    """
    url = (url or "").split("?")[0].split("#")[0].rstrip("/")
    for platform, pattern in resolvers:
        match = pattern.match(url)
        if match:
            handle = match.group("handle")
            if handle.lower() in _RESERVED_HANDLES:
                return None
            return platform, handle
    return None
