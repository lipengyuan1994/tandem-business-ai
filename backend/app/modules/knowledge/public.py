"""Stable module boundary. Public retrieval always filters visibility before ranking."""

from .schemas import SearchHit
from .service import search_public, search_staff, suggest

__all__ = ["SearchHit", "search_public", "search_staff", "suggest"]
