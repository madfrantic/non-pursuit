"""
Guided Wizard: walks through the same Self-Search and Letters screens in
sequence, with a step indicator on top. This does NOT reimplement either
screen's logic — it calls the exact same render() functions used by their
standalone sidebar entries, so there is exactly one implementation of each
to keep correct (letter validation, mailto safety, Spokeo automation, the
"confirm you're listed" gate all stay real, not a simplified stand-in).
"""
import streamlit as st

from components import self_search as self_search_component
from components import letters as letters_component

_STEPS = ["Search & confirm", "Generate & send"]


def render(brokers_df):
    st.header(":material/rocket_launch: Guided Wizard")

    step = st.session_state.get("wizard_step", 0)
    step = max(0, min(step, len(_STEPS) - 1))
    st.session_state.wizard_step = step

    st.progress((step + 1) / len(_STEPS), text=f"Step {step + 1} of {len(_STEPS)}: {_STEPS[step]}")
    st.markdown("---")

    if step == 0:
        self_search_component.render(brokers_df)
        st.markdown("---")
        if st.button(":material/arrow_forward: Continue to letters", type="primary", width="stretch"):
            st.session_state.wizard_step = 1
            st.rerun()
    else:
        letters_component.render(brokers_df)
        st.markdown("---")
        if st.button(":material/arrow_back: Back to search", width="stretch"):
            st.session_state.wizard_step = 0
            st.rerun()
