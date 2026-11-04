"""Permissions defined by the org module."""

BUSINESS_READ = "org.business.read"
BUSINESS_WRITE = "org.business.write"
BUSINESS_ADMIN = "org.business.admin"
LOCATION_READ = "org.location.read"
LOCATION_WRITE = "org.location.write"
SETTINGS_READ = "org.settings.read"
SETTINGS_WRITE = "org.settings.write"
AI_SETTINGS_WRITE = "ai.settings.write"  # defined by the ai module; org checks it for the ai_policy field

ALL = frozenset(
    {BUSINESS_READ, BUSINESS_WRITE, BUSINESS_ADMIN, LOCATION_READ, LOCATION_WRITE, SETTINGS_READ, SETTINGS_WRITE}
)
