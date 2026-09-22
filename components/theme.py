"""
The app's stylesheet, carried over from the pitch deck.

`demo_pitch.html` is where this product's visual language was actually
designed -- a 70s special-investigations dossier: midnight navy ground,
detective brass for anything authoritative, bone for body copy, stamp red
reserved for a deadline or a breach. The app looked nothing like it, so a
visitor who saw the deck and then opened the tool saw two products. The
tokens below are lifted from that file's `:root` block unchanged, and the
rules under them map those tokens onto Streamlit's own DOM.

WHY THE PALETTE IS FIXED RATHER THAN THEME-AWARE

Streamlit resolves its theme server-side from .streamlit/config.toml, but
with no [theme] section it falls back to whatever the browser reports --
so the app changed colour when the OS flipped to dark at sunset, and any
stylesheet written against `prefers-color-scheme` would disagree with the
widgets Streamlit had already painted from the other palette. The result
was a page that shifted mid-session and never quite matched itself.

Both halves of that are now pinned: config.toml declares the dark base
explicitly, and every colour here is a literal. There are no media
queries and no `[data-theme]` branches in this file on purpose -- a theme
that cannot change is a theme that cannot flicker. The deck is a dark
document; a light variant of it would be a different design, not a
setting.

WHY THE SELECTORS ARE MOSTLY data-testid

Streamlit's emitted class names are build hashes and change between
releases; the `data-testid` attributes are the part of that DOM the
framework treats as public. Anything targeted here that later disappears
degrades to an unstyled-but-working widget, which is why nothing below
uses `display:none` to hide chrome the app depends on.
"""
import streamlit as st

import debug_view

# --- tokens, lifted from demo_pitch.html :root ------------------------
NAVY = "#0B1325"
SLATE = "#152238"
SLATE_HI = "#1D2E4A"
BRASS = "#D4AF37"
BRASS_DIM = "#8A7328"
BONE = "#E8E2D4"
BONE_DIM = "#B4BDCA"  # lifted from #9AA3B2: 7.3:1 -> 9.8:1 on navy
EVIDENCE = "#FF3B30"
INK = "#060A14"

SERIF = ('"Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", '
         'Georgia, "Times New Roman", serif')
MONO = 'ui-monospace, "SF Mono", "Fira Code", "Cascadia Code", "Roboto Mono", Menlo, Consolas, monospace'
# system-ui first, not a webfont name that is probably not installed: the
# platform UI face is the one hinted and spaced for screen reading on that
# platform, and it needs no network round-trip to arrive.
SANS = ('system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, '
        '"Helvetica Neue", Arial, sans-serif')

_CSS = f"""
:root {{
  --np-navy: {NAVY};
  --np-slate: {SLATE};
  --np-slate-hi: {SLATE_HI};
  --np-brass: {BRASS};
  --np-brass-dim: {BRASS_DIM};
  --np-bone: {BONE};
  --np-bone-dim: {BONE_DIM};
  --np-evidence: {EVIDENCE};
  --np-ink: {INK};
  --np-serif: {SERIF};
  --np-mono: {MONO};
  --np-sans: {SANS};
  --np-hairline: rgba(212,175,55,.18);

  /* Type scale: nine steps on a ~1.2 ratio anchored at 15px, each one
     owning a role. The sheet had twelve unrelated sizes before this --
     0.74, 0.78, 0.80, 0.82, 0.88, 0.90, 0.92, 0.95rem and up -- which is
     what made the interface read as clunky: five of them sat within two
     pixels of each other, so they registered as noise rather than as
     hierarchy. Every font-size below resolves to one of these; a literal
     value anywhere else in this file is a bug. */
  --np-t-eyebrow: 0.875rem;   /* 14px - section headers, metric labels;
                                 the floor, and nothing renders below it */
  --np-t-small:   0.9375rem;  /* 15px - captions, widget labels */
  --np-t-body:    1rem;       /* 16px - body copy, inputs, nav, buttons */
  --np-t-lead:    1.125rem;   /* 18px - h4, card titles */
  --np-t-h3:      1.375rem;   /* 22px */
  --np-t-h2:      1.625rem;   /* 26px */
  --np-t-h1:      2rem;       /* 32px */
  --np-t-display: clamp(1.875rem, 3.2vw, 2.875rem);  /* the masthead only */
}}

/* ---------- ground ------------------------------------------------- */
/* The deck's radial is anchored to the top of the viewport. Fixed
   attachment keeps it there while the page scrolls, rather than tiling
   the gradient down a long form. */
[data-testid="stAppViewContainer"] {{
  background:
    radial-gradient(120% 90% at 50% 0%, #16233C 0%, var(--np-navy) 45%, var(--np-ink) 100%)
    fixed,
    var(--np-navy);
  color: var(--np-bone);
}}
[data-testid="stHeader"] {{ background: transparent; height: 1.5rem !important; }}
.block-container, [data-testid="stMainBlockContainer"], [data-testid="block-container"] {{
  padding-top: 1.5rem !important;
  padding-bottom: 2.5rem !important;
}}
[data-testid="stAppViewContainer"] .stMarkdown,
[data-testid="stAppViewContainer"] p,
[data-testid="stAppViewContainer"] li {{
  font-family: var(--np-sans);
  font-size: var(--np-t-body);
  line-height: 1.65;
  color: var(--np-bone);
}}

/* ---------- refined type scale ------------------------------------- */
/* Optical tracking: the larger a serif line is set, the tighter it wants
   to be. Each step below carries its own letter-spacing rather than
   inheriting one value across a 3x size range. */
h1, h2, h3, h4 {{
  font-family: var(--np-serif) !important;
  font-weight: 400 !important;
  color: var(--np-brass) !important;
}}
h1 {{
  font-size: var(--np-t-h1) !important;
  line-height: 1.18 !important;
  letter-spacing: -0.012em;
  text-shadow: 0 2px 0 rgba(0,0,0,.5), 0 0 62px rgba(212,175,55,.22);
}}
h2 {{
  font-size: var(--np-t-h2) !important;
  line-height: 1.24 !important;
  letter-spacing: -0.006em;
}}
h3 {{
  font-size: var(--np-t-h3) !important;
  line-height: 1.3 !important;
  letter-spacing: 0;
}}
h4 {{
  font-size: var(--np-t-lead) !important;
  line-height: 1.38 !important;
  letter-spacing: 0.004em;
  color: var(--np-bone) !important;
}}
/* h5/h6 are card titles in this app ("Core identity", "Your Opt-Out
   Campaign"). They were set in tracked uppercase mono at 12px, which is
   three legibility costs stacked on the smallest text in the card:
   monospace has no letterform variety, uppercase removes the word shape
   readers match on, and wide tracking breaks words into loose letters.
   Weight and colour carry the same emphasis and stay readable. */
h5, h6 {{
  font-family: var(--np-sans) !important;
  font-weight: 600 !important;
  font-size: var(--np-t-lead) !important;
  line-height: 1.4 !important;
  letter-spacing: 0;
  color: var(--np-bone) !important;
}}

/* Captions carry whole sentences of helper text, so they are set in the
   body sans. They were mono and tracked, which is a treatment for a
   two-word stamp: across a full-width sentence it cost real legibility
   and was the single clunkiest thing on the page. Mono is now reserved
   for things that are actually labels -- see the eyebrow rules. */
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{
  font-family: var(--np-sans) !important;
  font-size: var(--np-t-small) !important;
  letter-spacing: 0;
  color: var(--np-bone-dim) !important;
  line-height: 1.6;
}}

code, kbd, pre, [data-testid="stCode"] {{
  font-family: var(--np-mono) !important;
  font-size: var(--np-t-small) !important;
}}
a, a:visited {{ color: var(--np-brass); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
hr {{ border: 0; height: 1px; background: linear-gradient(90deg,
      var(--np-brass) 0%, rgba(212,175,55,.16) 55%, transparent 100%); }}

/* ---------- the headline ------------------------------------------- */
.np-masthead {{ margin: 0 0 1rem; }}
.np-masthead .np-headline {{
  font-family: var(--np-serif);
  font-size: var(--np-t-display);
  line-height: 1.06;
  letter-spacing: -0.018em;
  color: var(--np-brass);
  text-shadow: 0 2px 0 rgba(0,0,0,.5), 0 0 62px rgba(212,175,55,.22);
}}
.np-masthead .np-standfirst {{
  font-family: var(--np-sans);
  font-size: var(--np-t-small);
  letter-spacing: .01em;
  color: var(--np-bone-dim);
  margin-top: .55rem;
}}
.np-masthead .np-standfirst b {{ color: var(--np-brass); font-weight: 400; }}
.np-rule {{
  height: 1px; width: 100%;
  background: linear-gradient(90deg, var(--np-brass) 0%,
              rgba(212,175,55,.16) 55%, transparent 100%);
  margin: 12px 0 18px;
}}

/* ---------- sidebar ------------------------------------------------- */
[data-testid="stSidebar"] {{
  background: linear-gradient(180deg, var(--np-slate) 0%, var(--np-navy) 100%);
  border-right: 1px solid var(--np-hairline);
}}
/* A sidebar caption is a section header -- "Deep OSINT", "Sovereign
   Engine", "Recon & Audit" -- so it keeps the eyebrow treatment the body
   captions just gave up. */
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{
  font-family: var(--np-sans) !important;
  font-size: var(--np-t-eyebrow) !important;
  font-weight: 600 !important;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: var(--np-brass) !important;
}}
/* Except inside an expander, where a caption is running prose again
   ("Feature counts only -- no entered values are recorded."). Stamping
   that one would be the same mistake in a smaller place. */
[data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stCaptionContainer"] p {{
  font-family: var(--np-sans) !important;
  font-size: var(--np-t-small) !important;
  letter-spacing: 0;
  text-transform: none;
  color: var(--np-bone-dim) !important;
}}
[data-testid="stSidebar"] hr {{ margin: .75rem 0; }}
[data-testid="stSidebarNav"], [data-testid="stSidebar"] [data-testid="stPageLink"] a {{
  font-family: var(--np-sans);
  font-size: var(--np-t-body);
}}
[data-testid="stSidebar"] [data-testid="stPageLink"] a:hover {{
  background: rgba(212,175,55,.08);
}}
/* st.logo draws the shield here; give it room rather than scaling it down.
   position:relative is what the collapse button below anchors against --
   without it the button would pin to the viewport, not the rail.
   The right padding reserves that corner so a wide logo cannot slide
   under the button. */
[data-testid="stSidebarHeader"] {{
  position: relative !important;
  height: auto !important;
  min-height: 9.5rem !important;
  padding: 1.25rem 3rem 0.5rem 1rem !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
}}
/* 5.0rem -> 7.5rem, a 150% scale. max-width is the overflow guard: the
   sidebar is 21rem wide and a logo taller than it is wide would otherwise
   push the rail out rather than fit inside it. stSidebarLogo and
   stLogoLink are 1.4x's names for the same element; all three are listed
   so a version bump degrades to an unscaled logo rather than a broken
   header. */
[data-testid="stSidebarHeader"] img,
[data-testid="stLogo"], [data-testid="stLogo"] img,
[data-testid="stSidebarLogo"], [data-testid="stSidebarLogo"] img,
[data-testid="stLogoLink"] img {{
  max-height: 7.5rem !important;
  height: 7.5rem !important;
  width: auto !important;
  max-width: 100% !important;
  object-fit: contain !important;
}}
/* The collapse control pinned to the header's top-right corner.
   z-index clears the enlarged logo: both are in the same stacking
   context, and a 7.5rem logo centred in the header reaches further
   towards this corner than the 5rem one did. No pointer-events rule
   anywhere in this block -- the button has to stay clickable, and
   pointer-events:none on an ancestor is the usual way that breaks. */
[data-testid="stSidebarHeader"] [data-testid="stSidebarCollapseButton"] {{
  position: absolute !important;
  top: 0.5rem !important;
  right: 0.5rem !important;
  margin: 0 !important;
  z-index: 2 !important;
}}
/* The logo is decoration, not a link target, and at 7.5rem it covers most
   of the header. Letting clicks fall through it means a near-miss on the
   collapse button does nothing instead of navigating. */
[data-testid="stSidebarHeader"] [data-testid="stLogoSpacer"] {{
  pointer-events: none !important;
}}
[data-testid="stSidebar"] [data-testid="stRadio"] label p,
[data-testid="stSidebar"] [data-testid="stPageLink"] a span {{
  font-size: var(--np-t-body) !important;
}}
/* Sidebar prose -- alert bodies, help text -- sits a step below the main
   column. At body size it wrapped the runtime badge into six lines and
   made the rail feel like the page rather than the margin of it. */
[data-testid="stSidebar"] p, [data-testid="stSidebar"] li {{
  font-size: var(--np-t-small);
  line-height: 1.5;
}}
/* "Tools" is the nav's section header, not a control label -- it sits in
   the same rail as "Deep OSINT" and "Runtime environment", which are
   st.caption eyebrows, and was the one header in the sidebar wearing a
   different treatment. stWidgetLabel is the radio's own label; the
   options underneath are a separate node, so this does not reach them. */
[data-testid="stSidebar"] [data-testid="stRadio"] [data-testid="stWidgetLabel"] p {{
  font-family: var(--np-sans) !important;
  font-size: var(--np-t-eyebrow) !important;
  font-weight: 600 !important;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: var(--np-brass) !important;
}}
/* The per-option category label under each tool in the nav. It was
   inheriting the radio's own 15px through a more specific selector, so
   the category shouted over the tool it was categorising -- an inverted
   hierarchy, and the last thing in the sidebar still reading as clunky.
   Three attribute selectors is what it takes to outrank that rule. */
[data-testid="stSidebar"] [data-testid="stRadio"] [data-testid="stCaptionContainer"] p {{
  font-family: var(--np-sans) !important;
  font-size: var(--np-t-eyebrow) !important;
  font-weight: 500 !important;
  letter-spacing: .04em;
  text-transform: none;
  color: var(--np-bone-dim) !important;
}}

/* ---------- surfaces ------------------------------------------------ */
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) {{
  border-radius: 2px;
}}
[data-testid="stExpander"] {{
  border: 1px solid var(--np-hairline) !important;
  border-radius: 2px !important;
  background: linear-gradient(180deg, rgba(21,34,56,.92), rgba(11,19,37,.92));
}}
[data-testid="stExpander"] summary {{
  font-family: var(--np-sans);
  font-weight: 600;
  letter-spacing: 0;
  font-size: var(--np-t-small) !important;
  color: var(--np-brass);
}}
[data-testid="stMetric"] {{
  border: 1px solid var(--np-hairline);
  border-radius: 2px;
  background: var(--np-slate);
  padding: 12px 14px;
}}
[data-testid="stMetricValue"] {{
  font-family: var(--np-serif);
  color: var(--np-brass);
  font-size: var(--np-t-h2) !important;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
}}
[data-testid="stMetricLabel"] p {{
  font-family: var(--np-sans) !important;
  font-size: var(--np-t-eyebrow) !important;
  font-weight: 500 !important;
  letter-spacing: .02em;
  color: var(--np-bone-dim) !important;
}}

/* ---------- controls ------------------------------------------------ */
/* Buttons carry whole sentences of instruction ("Execute master recon",
   "Download complete audit trail"). Tracked uppercase mono at
   12px made the longest, most important controls in the app the hardest
   thing on the page to read. */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
  font-family: var(--np-sans);
  letter-spacing: .01em;
  font-size: var(--np-t-body);
  font-weight: 600;
  border-radius: 2px;
  border: 1px solid var(--np-brass-dim);
  transition: border-color .18s ease, background .18s ease;
  padding: 0.45rem 0.85rem;
}}
.stButton > button:hover, .stDownloadButton > button:hover,
.stFormSubmitButton > button:hover {{
  border-color: var(--np-brass);
  background: rgba(212,175,55,.1);
}}
/* Primary buttons are filled with the pinned primaryColor (brass,
   #D4AF37) and Streamlit paints their label in textColor -- bone,
   #E8E2D4. That is light-on-light at 1.63:1, under a fifth of the 4.5:1
   AA minimum, and it hit every primary control in the app: "Execute
   Master Recon", "Download Intelligence Dossier", "Open Intelligence
   Dossier", "All data (.json)". Black on brass is 9.99:1.

   Streamlit puts the label inside a nested <p>/<div>, so the colour has
   to be forced through to the descendants as well -- setting it on the
   button alone leaves the child element with its inherited bone. */
[data-testid="stBaseButton-primary"],
[data-testid="stBaseButton-primaryFormSubmit"] {{
  color: #000000 !important;
}}
[data-testid="stBaseButton-primary"] *,
[data-testid="stBaseButton-primaryFormSubmit"] * {{
  color: inherit !important;
}}
/* The generic hover rule above drops any button's background to 10%
   brass. On a secondary button that is a tint over the page; on a
   primary one it replaces the solid fill with the navy behind it, which
   would leave the black label at 1.35:1 -- worse than the bug being
   fixed. Primary keeps its fill on hover and moves the border instead. */
[data-testid="stBaseButton-primary"]:hover,
[data-testid="stBaseButton-primary"]:focus,
[data-testid="stBaseButton-primary"]:active,
[data-testid="stBaseButton-primaryFormSubmit"]:hover,
[data-testid="stBaseButton-primaryFormSubmit"]:focus,
[data-testid="stBaseButton-primaryFormSubmit"]:active {{
  background: var(--np-brass) !important;
  border-color: var(--np-bone) !important;
  color: #000000 !important;
}}
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
[data-testid="stTextArea"] textarea, [data-testid="stDateInput"] input {{
  border-radius: 2px;
  font-family: var(--np-sans);
  font-size: var(--np-t-body);
}}
/* Widget labels are sans too. "First", "Email", "Username / handle" are
   form labels, not stamps -- mono and tracking made a plain name field
   look like a terminal prompt, and weight carries the emphasis more
   quietly than letter-spacing does at this size. */
[data-testid="stTextInput"] label p, [data-testid="stSelectbox"] label p,
[data-testid="stTextArea"] label p, [data-testid="stDateInput"] label p,
[data-testid="stNumberInput"] label p, [data-testid="stRadio"] label p,
[data-testid="stToggle"] label p, [data-testid="stFileUploader"] label p,
[data-testid="stSlider"] label p, [data-testid="stMultiSelect"] label p {{
  font-family: var(--np-sans) !important;
  font-size: var(--np-t-small) !important;
  font-weight: 500 !important;
  letter-spacing: .01em;
  color: var(--np-bone) !important;
}}
/* Tab labels are deliberately left alone. Streamlit 1.62 renders them
   through a BaseWeb structure with no data-testid and no role="tab" on
   the clickable element, so every selector that reaches them is a build
   hash -- and they read correctly in the body sans anyway. A rule that
   matches nothing is worse than no rule: it claims a style the page does
   not have. */

/* ---------- status --------------------------------------------------- */
/* Red is the deck's evidence stamp and stays scarce -- errors only. The
   45-day clock is the other thing allowed to wear it, and it does so
   through .np-clock rather than by repainting every alert. */
[data-testid="stAlert"] {{ border-radius: 2px; border-left-width: 3px; }}
/* The clock keeps mono -- it is a countdown, and tabular digits stop the
   number jittering as it ticks. Tracking comes off; it was spacing the
   digits apart rather than aligning them. */
.np-clock {{
  font-family: var(--np-mono);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0;
  color: var(--np-evidence);
}}

/* ---------- tables --------------------------------------------------- */
[data-testid="stDataFrame"], [data-testid="stTable"] {{
  border: 1px solid var(--np-hairline);
  border-radius: 2px;
}}
[data-testid="stTable"] td, [data-testid="stTable"] th {{
  font-family: var(--np-sans);
  font-size: var(--np-t-body);
  font-variant-numeric: tabular-nums;
}}
[data-testid="stTable"] th {{
  font-family: var(--np-sans);
  font-size: var(--np-t-eyebrow);
  font-weight: 600;
  letter-spacing: .02em;
  color: var(--np-bone-dim);
}}

@media (prefers-reduced-motion: reduce) {{
  * {{ transition-duration: .12s !important; animation-duration: .01ms !important; }}
}}
"""


def css() -> str:
    """The stylesheet as a string. Exposed so a test can assert against it
    without needing a Streamlit runtime."""
    return _CSS


def inject() -> None:
    """Apply the stylesheet. Called once per script run.

    A no-op under NON_PURSUIT_DEBUG_UNSTYLE: debug_view exists to strip
    styling so a broken layout can be inspected, and injecting this
    afterwards would put it straight back.

    Deliberately unguarded otherwise. Streamlit rebuilds the DOM on every
    rerun, so a "only inject once per session" guard would style the first
    render and leave every one after it bare -- the style block has to be
    re-emitted each run, and app.py and each pages/ script are separate
    runs that each call this exactly once.
    """
    if debug_view.enabled():
        return
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)


def masthead(headline: str, standfirst: str = "") -> None:
    """The one headline block, drawn the way the deck's slide 1 draws it.

    Written as markup rather than st.title because the headline is
    lowercase by design and Streamlit's heading styles are the wrong
    scale for it -- and because the standfirst underneath is the deck's
    mono .label, which has no Streamlit equivalent.
    """
    sub = f'<div class="np-standfirst">{standfirst}</div>' if standfirst else ""
    st.markdown(
        f'<div class="np-masthead"><div class="np-headline">{headline}</div>'
        f'{sub}</div><div class="np-rule"></div>',
        unsafe_allow_html=True,
    )
