"""TokenVeil Community Edition — application package.

Runtime resources (encrypted DB, linked accounts, static/, tools/) live at the
PROJECT ROOT, not inside the package. The first-party code was moved under
src/tokenveil/ (2026-07 tidy-up); PROJECT_ROOT re-anchors every path there
instead of os.path.dirname(__file__), which now points inside the package.
"""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
