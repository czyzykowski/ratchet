"""Shared Jinja2Templates instance for web routes."""

from __future__ import annotations

import pathlib

from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
