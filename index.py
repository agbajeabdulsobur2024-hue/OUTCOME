"""Vercel FastAPI entrypoint. Vercel detects the exported ASGI app."""
from app.main import app

__all__ = ["app"]
