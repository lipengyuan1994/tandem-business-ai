"""Public dashboard API: typed, permission-filtered reads in a caller-owned transaction."""

from .service import TodaySummary, today_summary

__all__ = ["TodaySummary", "today_summary"]
