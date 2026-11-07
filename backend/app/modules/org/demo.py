"""D5 demo statement and installation boundary (no clinical compliance claim)."""

from sqlalchemy import text

from app.platform.config import is_regulated
from app.platform.db import get_engine

SYNTHETIC_DEMO_BANNER = (
    "Synthetic demo data. In production, patients' protected health information (PHI) and other personal data must be "
    "processed only by AI models hosted locally under the clinic's control — no external AI API. Compliance also "
    "needs the clinic's HIPAA policies, agreements and a legal review."
)


def banner(synthetic_demo: bool) -> str | None:
    return SYNTHETIC_DEMO_BANNER if synthetic_demo else None


def assert_no_synthetic_demos() -> None:
    if is_regulated():
        with get_engine().connect() as conn:
            if conn.scalar(text("select synthetic_demo_exists()")):
                raise RuntimeError(
                    "DATA_PROFILE=regulated refuses synthetic demo businesses; remove demo data before starting."
                )
