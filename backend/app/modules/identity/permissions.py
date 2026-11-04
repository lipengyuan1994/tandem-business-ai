"""Permissions defined by the identity module."""

MEMBERSHIP_READ = "identity.membership.read"
MEMBERSHIP_WRITE = "identity.membership.write"
OWNER_GRANT = "identity.owner.grant"

ALL = frozenset({MEMBERSHIP_READ, MEMBERSHIP_WRITE, OWNER_GRANT})
