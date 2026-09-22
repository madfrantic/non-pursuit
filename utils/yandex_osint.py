"""
Yandex reverse-image-search link builder.

Same shape as google_dork.py: a URL, not a scrape. Yandex's actual image
upload flow (POST to images-apphost/image-download) is an internal,
undocumented endpoint that expects a live browser session -- cookies and
a CSRF token a plain request can't produce. Confirmed by hand before
writing this file: a bare multipart POST against it returns
"400 Incorrect avatar size" regardless of image size, which is what that
endpoint says when the required session state is missing, not when the
image itself is wrong.

Yandex's own search-by-URL parameter needs none of that: it works for
anyone, unauthenticated, provided the image is already reachable at a
public URL -- which every avatar in this app already is (Gravatar,
Libravatar, a discovered profile's own CDN). Nothing is uploaded from
this machine; Yandex fetches the URL itself.
"""
from urllib.parse import quote_plus

YANDEX_IMAGE_SEARCH_URL = "https://yandex.com/images/search"


def build_reverse_image_search_url(image_url: str) -> str:
    """A Yandex reverse-image search targeted at an already-public image
    URL. Returns "" for no URL -- callers use that to skip rendering the
    button rather than link to a broken search."""
    if not image_url:
        return ""
    return f"{YANDEX_IMAGE_SEARCH_URL}?rpt=imageview&url={quote_plus(image_url)}"
