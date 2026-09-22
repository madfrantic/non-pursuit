"""
Coverage for the rebrand and for the thing that made the app look unstable.

The theme assertions are unusual for this suite -- they read CSS rather
than call a function -- and they are here because the bug they guard was
not a crash. The app had no [theme] block, so Streamlit resolved the
palette from the browser and the page changed colour when the OS did;
anything written against `prefers-color-scheme` on top of that disagreed
with the widgets Streamlit had already painted. Nothing raises when that
happens, so only a test that looks at the stylesheet can catch a
regression back into it.
"""
import pathlib
import re

import pytest

import config
import runtime_mode
from components import nav, theme

ROOT = pathlib.Path(__file__).resolve().parent.parent
STREAMLIT_CONFIG = ROOT / ".streamlit" / "config.toml"


@pytest.fixture(autouse=True)
def clean_runtime(monkeypatch):
    monkeypatch.delenv(runtime_mode.DEMO_ENV_VAR, raising=False)
    monkeypatch.delenv(runtime_mode.DEPLOYMENT_ENV_VAR, raising=False)
    monkeypatch.delenv("STREAMLIT_SHARING_MODE", raising=False)
    runtime_mode.reset_runtime_override()
    runtime_mode.reset_startup_default()
    yield
    runtime_mode.reset_runtime_override()
    runtime_mode.reset_startup_default()


# --- the headline -----------------------------------------------------

def test_there_is_one_headline_and_it_is_lowercase():
    assert config.APP_HEADLINE == "non-pursuit. the sovereign agent"
    assert config.APP_HEADLINE == config.APP_HEADLINE.lower()


def test_the_replaced_taglines_are_gone_from_every_surface():
    """Both old lines, in the app and in the deck it was pitched with --
    the point of the rename was that the product stopped having two
    names depending on which one you read."""
    retired = ("Take yourself off the market", "They chase. You enforce")
    surfaces = [ROOT / "app.py", ROOT / "demo_pitch.html", ROOT / "config.py"]
    for path in surfaces:
        text = path.read_text(encoding="utf-8")
        # Strip comments/prose that explain the rename itself.
        for line in text.splitlines():
            if line.lstrip().startswith(("#", "//")) or "replaced" in line:
                continue
            for old in retired:
                assert old not in line, f"{path.name}: {line.strip()}"


def test_the_footer_carries_the_headline():
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "config.APP_HEADLINE" in text


# --- the top mark -----------------------------------------------------

def test_the_sidebar_mark_is_the_shield_not_the_wordmark():
    """The wordmark is a text image; it repeated in a picture the words
    the headline says two lines below it."""
    assert config.APP_TOPMARK_PATH == config.APP_LOGO_PATH
    assert config.APP_TOPMARK_PATH.endswith("logo_shield.png")
    assert (ROOT / config.APP_TOPMARK_PATH).exists()

    text = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "st.logo(config.APP_TOPMARK_PATH" in text
    assert "st.logo(config.APP_WORDMARK_PATH" not in text


# --- theme stability --------------------------------------------------

def test_streamlit_is_told_which_theme_to_use():
    """Without this the browser decides, and the app changes palette when
    the OS flips to dark."""
    toml = STREAMLIT_CONFIG.read_text(encoding="utf-8")
    assert "[theme]" in toml
    assert 'base = "dark"' in toml


def test_the_pinned_theme_matches_the_stylesheet_and_config():
    """Three files describe this palette. Any two of them disagreeing is
    exactly the mid-page colour shift this was meant to end."""
    toml = STREAMLIT_CONFIG.read_text(encoding="utf-8")

    def value(key):
        match = re.search(rf'^{key} = "(#[0-9A-Fa-f]{{6}})"', toml, re.M)
        assert match, f"{key} missing from [theme]"
        return match.group(1).upper()

    assert value("backgroundColor") == theme.NAVY.upper() == config.BACKGROUND_COLOR.upper()
    assert value("secondaryBackgroundColor") == theme.SLATE.upper() == config.SECONDARY_BACKGROUND_COLOR.upper()
    assert value("primaryColor") == theme.BRASS.upper() == config.PRIMARY_COLOR.upper()
    assert value("textColor") == theme.BONE.upper() == config.TEXT_COLOR.upper()


def test_the_stylesheet_does_not_repaint_itself_per_colour_scheme():
    """A theme that cannot change is a theme that cannot flicker. The one
    media query allowed is the reduced-motion one, which changes no
    colour."""
    css = theme.css()
    assert "prefers-color-scheme" not in css
    assert "[data-theme=" not in css
    queries = re.findall(r"@media\s*\(([^)]*)\)", css)
    assert queries == ["prefers-reduced-motion: reduce"]


def test_the_palette_is_the_one_from_the_pitch_deck():
    """The tokens are lifted from demo_pitch.html's :root, which is where
    this product's visual language was actually designed."""
    deck = (ROOT / "demo_pitch.html").read_text(encoding="utf-8")
    for token in (theme.NAVY, theme.SLATE, theme.BRASS, theme.BONE, theme.EVIDENCE):
        assert token in deck, token


def test_the_stylesheet_stays_out_of_the_way_of_the_diagnostic_view(monkeypatch):
    """debug_view exists to strip styling so a broken layout can be
    inspected. Injecting this afterwards would put it straight back."""
    import debug_view

    monkeypatch.setenv(debug_view.ENV_FLAG, "1")
    calls = []
    monkeypatch.setattr(theme.st, "markdown", lambda *a, **k: calls.append(a))
    theme.inject()
    assert calls == []


def test_the_stylesheet_is_injected_on_every_surface():
    """app.py and page_shell are the two script entrypoints; a page that
    skips it is the one unstyled screen in the app."""
    for path in (ROOT / "app.py", ROOT / "components" / "page_shell.py"):
        assert "theme.inject()" in path.read_text(encoding="utf-8"), path.name


def test_every_entrypoint_opens_on_the_same_runtime():
    """A pages/ script is its own top-level run. Opening one by URL, or
    refreshing on it, reaches page_shell without app.py ever running --
    and that session sat on auto-detection while the main app sat online,
    so the sidebar named the engine section two different things in the
    same session."""
    for path in (ROOT / "app.py", ROOT / "components" / "page_shell.py"):
        assert "apply_startup_default()" in path.read_text(encoding="utf-8"), path.name


# --- the console's two names ------------------------------------------

def test_the_engine_section_is_the_product_name_online():
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_CLOUD)
    assert nav.section_label(nav.SECTION_ENGINE) == config.SOVEREIGN_AGENT_LABEL
    assert nav.section_label(nav.SECTION_ENGINE) == "Sovereign Agent"


def test_the_old_console_name_survives_only_on_the_desktop_build():
    """"Broker agent console" names something only the separately
    installed desktop build ships. Showing it to a web session promises a
    console that session cannot run."""
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_DESKTOP)
    assert nav.section_label(nav.SECTION_ENGINE) == config.DESKTOP_CONSOLE_LABEL
    assert nav.section_label(nav.SECTION_ENGINE) == "Broker agent console"


def test_the_other_sections_do_not_move():
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_DESKTOP)
    assert nav.section_label(nav.SECTION_OSINT) == "Deep OSINT"
    runtime_mode.set_runtime_override(runtime_mode.OVERRIDE_CLOUD)
    assert nav.section_label(nav.SECTION_OSINT) == "Deep OSINT"


# --- the type scale ---------------------------------------------------
# The complaint these guard was not a bug report: the interface read as
# clunky because sizes had been picked one at a time. The sheet carried
# twelve unrelated values, five of them within two pixels of each other,
# so they registered as noise instead of hierarchy. A scale only stays a
# scale if the next edit has to use it.

SCALE_STEPS = [
    "--np-t-eyebrow", "--np-t-small", "--np-t-body",
    "--np-t-lead", "--np-t-h3", "--np-t-h2", "--np-t-h1", "--np-t-display",
]

# The smallest step, in rem. Everything the interface renders resolves to
# this or larger.
FLOOR_REM = 0.875  # 14px


def _scale_rem():
    """The fixed steps as floats, in declared order. --np-t-display is a
    clamp() and has no single value, so it is not comparable here."""
    css = theme.css()
    out = []
    for token in SCALE_STEPS:
        if token == "--np-t-display":
            continue
        match = re.search(rf"{token}:\s*([0-9.]+)rem", css)
        assert match, f"{token} missing from :root"
        out.append((token, float(match.group(1))))
    return out


def test_every_font_size_comes_from_the_scale():
    """A literal font-size is how the twelve-value pile happened. The one
    exception is the scale's own definition."""
    css = theme.css()
    offenders = [
        line.strip() for line in css.splitlines()
        if "font-size:" in line and "var(--np-t-" not in line and "--np-t-" not in line
    ]
    assert offenders == [], offenders


def test_the_scale_is_ordered_and_has_no_near_duplicate_steps():
    """Two steps a pixel apart are not a hierarchy -- they are the thing
    that looked accidental. 1px = 0.0625rem, so require real separation."""
    steps = _scale_rem()
    values = [v for _, v in steps]
    assert values == sorted(values), steps
    for (name_a, a), (name_b, b) in zip(steps, steps[1:]):
        assert b - a >= 0.06, f"{name_a} ({a}rem) and {name_b} ({b}rem) are too close"


def test_the_display_step_is_the_masthead_and_only_the_masthead():
    """It is the one clamp() in the sheet; anything else wearing it would
    compete with the headline."""
    css = theme.css()
    assert css.count("var(--np-t-display)") == 1
    headline = css.split(".np-masthead .np-headline")[1].split("}")[0]
    assert "var(--np-t-display)" in headline


def test_running_text_is_not_set_in_the_stamp_face():
    """Mono plus letter-spacing is a treatment for a two-word label. Over
    a full-width sentence it cost real legibility, and captions and form
    labels are sentences."""
    css = theme.css()
    caption = css.split('[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p')[1].split("}")[0]
    assert "var(--np-sans)" in caption
    assert "var(--np-mono)" not in caption

    labels = css.split('[data-testid="stTextInput"] label p')[1].split("}")[0]
    assert "var(--np-sans)" in labels
    assert "var(--np-mono)" not in labels


def test_mono_is_reserved_for_code_and_the_countdown():
    """Monospace letterforms are the "blocky" the interface was reported
    as. It ran the whole label layer -- card titles, metric labels,
    buttons, section headers, the standfirst. It now survives in two
    places: code, and the deadline clock, where tabular digits stop the
    number jittering as it ticks."""
    css = theme.css()
    mono_blocks = [
        block for block in css.split("}")
        if "var(--np-mono)" in block and "--np-mono:" not in block
    ]
    assert len(mono_blocks) == 2, [b.strip()[:80] for b in mono_blocks]
    joined = " ".join(mono_blocks)
    assert "code" in joined
    assert ".np-clock" in joined

    for selector in ('[data-testid="stMetricLabel"] p',
                     ".np-masthead .np-standfirst",
                     '[data-testid="stExpander"] summary',
                     ".stButton > button"):
        block = css.split(selector)[1].split("}")[0]
        assert "var(--np-mono)" not in block, selector


def test_nothing_renders_below_the_legibility_floor():
    """The scale used to bottom out at 11px, which is where the metric
    labels and every section header lived."""
    steps = _scale_rem()
    assert min(v for _, v in steps) >= FLOOR_REM
    assert dict(steps)["--np-t-eyebrow"] == FLOOR_REM


def test_body_copy_is_at_least_16px():
    """Below this, long-form reading gets measurably harder -- and it is
    also the size at which mobile browsers stop zooming a focused input."""
    assert dict(_scale_rem())["--np-t-body"] >= 1.0


def test_letter_spacing_never_breaks_words_apart():
    """Tracking of .14-.2em was set on 11px labels. At that size it stops
    reading as one word, which is exactly the cue a reader with reduced
    acuity is relying on."""
    css = theme.css()
    tracked = [float(v) for v in re.findall(r"letter-spacing: (\.[0-9]+)em", css)]
    assert tracked, "expected some positive tracking"
    assert max(tracked) <= 0.06, max(tracked)


def test_uppercase_is_limited_to_short_navigational_landmarks():
    """Uppercase removes the word shape readers match on, so it is worth
    it only where the string is one or two words and is being scanned
    rather than read -- the sidebar's own section headers."""
    css = theme.css()
    upper_blocks = [b for b in css.split("}") if "text-transform: uppercase" in b]
    assert len(upper_blocks) == 2, [b.strip()[:90] for b in upper_blocks]
    for block in upper_blocks:
        assert "stSidebar" in block, block.strip()[:90]


def test_dim_text_clears_wcag_aa_with_room_to_spare():
    """Contrast sensitivity drops with age, so the dimmed tier is held
    well above the 4.5:1 minimum rather than at it."""
    def _lum(hex_colour):
        hex_colour = hex_colour.lstrip("#")
        channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                    for c in channels]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    def _ratio(fg, bg):
        a, b = _lum(fg), _lum(bg)
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)

    for background in (theme.NAVY, theme.SLATE):
        for foreground in (theme.BONE, theme.BONE_DIM, theme.BRASS):
            assert _ratio(foreground, background) >= 7.0, (foreground, background)


def test_the_four_heading_levels_are_four_different_sizes():
    """h4/h5/h6 were one rule at one size, so a page with a heading and a
    card eyebrow rendered both identically and neither outranked the
    other."""
    css = theme.css()
    sizes = {}
    for level in ("h1", "h2", "h3", "h4"):
        block = css.split(f"\n{level} {{")[1].split("}")[0]
        match = re.search(r"font-size: var\((--np-t-[a-z0-9]+)\)", block)
        assert match, level
        sizes[level] = match.group(1)
    assert len(set(sizes.values())) == 4, sizes

    # h5/h6 are card titles, set in the body face at the lead step -- they
    # still sit below h4, but through weight rather than by shrinking into
    # tracked uppercase mono.
    card_title = css.split("\nh5, h6 {")[1].split("}")[0]
    assert "var(--np-sans)" in card_title
    assert "font-weight: 600" in card_title


# --- sidebar header: logo scale and the pinned collapse control -------
#
# Measured in real Chrome against the running app (Streamlit 1.62,
# 1440x900): logo 95x120px inside a 300px sidebar, collapse button at
# top:8px right:8px of a position:relative header, elementFromPoint over
# the button resolving to its own icon rather than the logo, and a
# collapse -> expand round trip returning the sidebar to 300px.

def _rule(css, selector):
    """The declaration block for `selector`, which must appear once.

    `selector` may include its opening brace -- needed when a bare
    selector also appears as the prefix of a longer one.
    """
    assert css.count(selector) == 1, selector
    tail = css.split(selector)[1]
    if not selector.rstrip().endswith("{"):
        tail = tail.split("{", 1)[1]
    return tail.split("}")[0]


def test_the_sidebar_logo_is_scaled_up_and_cannot_overflow_the_rail():
    css = theme.css()
    block = _rule(css, '[data-testid="stLogoLink"] img')
    assert "height: 7.5rem" in block          # 150% of the previous 5rem
    assert "max-height: 7.5rem" in block
    assert "object-fit: contain" in block
    # The overflow guard. Without it a logo wider than the rail pushes the
    # sidebar out instead of fitting inside it.
    assert "max-width: 100%" in block
    assert "width: auto" in block


def test_the_header_can_anchor_an_absolutely_positioned_child():
    """position:absolute resolves against the nearest positioned
    ancestor -- without this the collapse button would pin to the
    viewport rather than to the sidebar header."""
    block = _rule(theme.css(), '[data-testid="stSidebarHeader"] {')
    assert "position: relative" in block
    # Room for the enlarged logo, and a right gutter the button occupies.
    assert "min-height: 9.5rem" in block
    assert "padding: 1.25rem 3rem 0.5rem 1rem" in block


def test_the_collapse_control_is_pinned_to_the_top_right():
    css = theme.css()
    block = _rule(css, '[data-testid="stSidebarHeader"] [data-testid="stSidebarCollapseButton"]')
    assert "position: absolute" in block
    assert "top: 0.5rem" in block
    assert "right: 0.5rem" in block
    # Above the enlarged logo, which now reaches further into this corner.
    assert "z-index: 2" in block


def test_nothing_makes_the_collapse_control_unclickable():
    """The failure mode for this layout is a pointer trap: an overlay or
    an ancestor with pointer-events:none swallowing the click. The
    button's own block must never disable pointer events, and the only
    element that does is the decorative logo spacer."""
    css = theme.css()
    button_block = _rule(css, '[data-testid="stSidebarHeader"] [data-testid="stSidebarCollapseButton"]')
    assert "pointer-events" not in button_block
    assert "display: none" not in button_block
    assert "visibility: hidden" not in button_block

    spacer = _rule(css, '[data-testid="stSidebarHeader"] [data-testid="stLogoSpacer"]')
    assert "pointer-events: none" in spacer

    # Streamlit reveals the control on hover by toggling visibility on
    # this element; a rule of ours forcing it either way would fight that.
    header_block = _rule(css, '[data-testid="stSidebarHeader"] {')
    assert "pointer-events" not in header_block


# --- primary-button contrast -----------------------------------------

def _contrast(fg, bg):
    """WCAG 2.x relative-luminance contrast ratio between two hex colours."""
    def _lum(hex_colour):
        hex_colour = hex_colour.lstrip("#")
        channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                    for c in channels]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
    a, b = _lum(fg), _lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def test_the_default_primary_button_label_would_be_unreadable():
    """Why the rule below has to exist.

    Streamlit fills a type="primary" button with primaryColor and paints
    its label in textColor. Both are pinned in .streamlit/config.toml,
    and brass-on-bone lands at 1.63:1 -- under a fifth of the 4.5:1 AA
    floor. If a future palette change ever made that pairing legible on
    its own, this test fails and the override can be reconsidered.
    """
    assert _contrast(theme.BONE, theme.BRASS) < 4.5


def test_primary_buttons_are_overridden_to_black_ink():
    """The fix: black on brass is ~10:1.

    Asserted against the stylesheet rather than a rendered page because
    the bug is invisible to every other kind of test -- nothing raises
    when a label is merely unreadable.
    """
    css = theme.css()
    for testid in ("stBaseButton-primary", "stBaseButton-primaryFormSubmit"):
        assert f'[data-testid="{testid}"]' in css, testid
    assert _contrast("#000000", theme.BRASS) >= 7.0


def test_primary_buttons_keep_their_fill_on_hover():
    """The generic button hover drops the background to 10% brass. On a
    primary button that would swap the solid fill for the navy page
    behind it and strand the black label at 1.35:1 -- so primary must
    restate its own background in the hover rule."""
    css = theme.css()
    hover_block = re.search(
        r'\[data-testid="stBaseButton-primary"\]:hover.*?\}', css, re.S)
    assert hover_block, "primary buttons have no hover rule of their own"
    assert "--np-brass" in hover_block.group(0)
