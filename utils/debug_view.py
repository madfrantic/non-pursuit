"""
Diagnostic view: neutralise styling and force hidden elements visible.

Temporary debugging aid, off unless NON_PURSUIT_DEBUG_UNSTYLE is set. It
adds nothing to a normal run -- `inject()` returns immediately when the
flag is absent -- so reverting is unsetting an environment variable, not
undoing an edit.

WHY THIS INJECTS CSS INSTEAD OF REMOVING IT

There is no stylesheet in this repo to comment out. Non-Pursuit has no
.css file, no <style> block, no CSS-in-JS and no utility-class framework;
every pixel of its appearance comes from the CSS Streamlit bundles inside
its own React build, served from the installed package. Application code
cannot unlink that.

So "strip the CSS" here means overriding it: a reset with enough
specificity to flatten Streamlit's own rules back toward browser defaults.
It is not identical to serving the page with no stylesheet at all --
Streamlit's layout is partly structural (flex containers it emits as
markup) and some of that survives. It is close enough to read the document
structure, which is the point.

WHAT IT REVEALS

The same pass forces visible everything the app hides:

  * collapsed widget labels (`label_visibility="collapsed"`), which are the
    only deliberately hidden *text* in this codebase;
  * anything carrying [hidden] or aria-hidden;
  * the chrome Streamlit hides by default (toolbar, status widget, footer);
  * collapsed expander bodies, so their content is readable in place.

Elements that are hidden by *Python* rather than CSS cannot be revealed
this way -- an `if` that never runs emits no markup for a stylesheet to
reach. Those are listed in the audit that accompanies this module rather
than being forced on here, because forcing them would mean changing app
logic, which a diagnostic view has no business doing.
"""
from __future__ import annotations

import os

ENV_FLAG = "NON_PURSUIT_DEBUG_UNSTYLE"

# Outline colour per nesting concern. Deliberately garish: this view exists
# to be read, not admired.
_RESET_CSS = """
/* ---------------------------------------------------------------- reset */
/* Flatten Streamlit's bundled rules toward browser defaults. !important
   throughout because we are overriding a stylesheet that already uses
   high-specificity generated class names. */
.stApp, .stApp *,
[data-testid="stAppViewContainer"], [data-testid="stAppViewContainer"] * {
    background: #ffffff !important;
    color: #000000 !important;
    font-family: Times, "Times New Roman", serif !important;
    font-size: 16px !important;
    font-weight: normal !important;
    line-height: 1.4 !important;
    letter-spacing: normal !important;
    text-transform: none !important;
    box-shadow: none !important;
    border-radius: 0 !important;
    text-shadow: none !important;
    transition: none !important;
    animation: none !important;
    transform: none !important;
    filter: none !important;
    backdrop-filter: none !important;
    max-width: none !important;
    min-height: 0 !important;
    gap: 0 !important;
}

/* Layout containers back to plain block flow so nesting reads top-down. */
.stApp [class*="st-emotion"], .stApp [class*="css-"],
[data-testid="stVerticalBlock"], [data-testid="stHorizontalBlock"],
[data-testid="column"], [data-testid="stMain"] .block-container {
    display: block !important;
    flex: none !important;
    width: auto !important;
    padding: 0 !important;
    margin: 0 0 0.35rem 0 !important;
}

h1, h2, h3, h4, h5, h6 { font-weight: bold !important; margin: 0.6em 0 0.3em !important; }
h1 { font-size: 2em !important; } h2 { font-size: 1.5em !important; }
h3 { font-size: 1.17em !important; }
a { color: #0000ee !important; text-decoration: underline !important; }
button, input, select, textarea {
    border: 1px solid #767676 !important;
    background: #efefef !important;
    padding: 2px 6px !important;
}
table, th, td { border: 1px solid #000 !important; border-collapse: collapse !important; }

/* ------------------------------------------------------------- reveal */
/* Force visible everything hidden by styling, including Streamlit's own
   chrome and any collapsed widget label. */
.stApp *, .stApp *::before, .stApp *::after,
[data-testid="stAppViewContainer"] * {
    visibility: visible !important;
    opacity: 1 !important;
    clip: auto !important;
    clip-path: none !important;
    text-indent: 0 !important;
    height: auto !important;
    width: auto !important;
    overflow: visible !important;
    position: static !important;
}

/* Collapsed labels are the only deliberately hidden text in this app. */
[data-testid="stWidgetLabel"],
[data-testid="stWidgetLabel"] *,
label, label * {
    display: block !important;
    visibility: visible !important;
    opacity: 1 !important;
}

/* Anything the markup itself marks hidden. */
[hidden], [aria-hidden="true"], .sr-only, .visually-hidden {
    display: revert !important;
    visibility: visible !important;
    opacity: 1 !important;
}

/* Streamlit chrome that ships hidden or de-emphasised. */
header, [data-testid="stHeader"], [data-testid="stToolbar"],
[data-testid="stDecoration"], [data-testid="stStatusWidget"],
#MainMenu, footer {
    display: block !important;
    visibility: visible !important;
    height: auto !important;
}

/* Open every collapsed expander body in place. */
[data-testid="stExpander"] details { }
[data-testid="stExpander"] details > div,
[data-testid="stExpanderDetails"] {
    display: block !important;
    visibility: visible !important;
    height: auto !important;
    overflow: visible !important;
}

/* ------------------------------------------------------------ outlines */
/* Structure made legible: every block gets a hairline, and the elements
   this view exists to surface get a labelled, coloured one. */
.stApp * { outline: 1px solid rgba(0, 0, 0, 0.14) !important; }

[data-testid="stWidgetLabel"] {
    outline: 2px solid #d00 !important;
    background: #ffe9e9 !important;
}
[hidden], [aria-hidden="true"] {
    outline: 2px dashed #b8860b !important;
    background: #fff8e1 !important;
}
[data-testid="stExpander"] {
    outline: 2px solid #06c !important;
    background: #eef5ff !important;
}
[data-testid="stSidebar"] { outline: 2px solid #060 !important; }

/* A visible marker so nobody mistakes this for the real UI. */
[data-testid="stAppViewContainer"]::before {
    content: "DEBUG UNSTYLE ACTIVE - styling neutralised, hidden elements forced visible" !important;
    display: block !important;
    background: #d00 !important;
    color: #fff !important;
    font-family: monospace !important;
    font-size: 13px !important;
    padding: 6px 10px !important;
    position: sticky !important;
    top: 0 !important;
    z-index: 99999 !important;
}
"""


def enabled() -> bool:
    """True when the diagnostic view is switched on."""
    return os.getenv(ENV_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def inject() -> None:
    """Apply the diagnostic stylesheet. A no-op unless the flag is set.

    Called once per script run from app.py and from page_shell.setup(), so
    every page is covered by the same single switch.
    """
    if not enabled():
        return
    import streamlit as st

    st.markdown(f"<style>{_RESET_CSS}</style>", unsafe_allow_html=True)
