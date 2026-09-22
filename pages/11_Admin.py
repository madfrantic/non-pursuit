"""
Owner console — every record this install holds, in one place.

Not part of the app a visitor uses. The sidebar drops the link for anyone
who has not unlocked it (components/nav.ADMIN_ONLY_SECTIONS), but a link
that is merely not drawn is not a permission check -- somebody who types
the URL lands here, so the gate is enforced in this file too.

The import order below is the same one every other page in pages/ uses,
and it is load-bearing rather than stylistic: page_shell.setup() is what
puts utils/ on sys.path, and components/admin_dashboard.py imports
admin_auth from there. Importing the dashboard first works only in a
process where app.py has already run -- so the page rendered fine when
opened from the sidebar and raised ModuleNotFoundError for anyone who
opened its URL directly or refreshed on it.
"""
from components import page_shell

page_shell.setup("Owner console", icon="🗄️")

import streamlit as st

from components import admin_dashboard

if not admin_dashboard.require_admin():
    st.stop()

admin_dashboard.render()
