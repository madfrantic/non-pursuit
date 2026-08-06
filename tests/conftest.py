"""
Makes utils/ importable the same way app.py does (flat imports, no package),
so tests can `import mailto_builder` / `import tracker` directly.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "utils"))
