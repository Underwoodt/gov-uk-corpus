"""System roles for feature gating.

Three roles — Administrator, User, Tester. For now there is ONE global active role,
stored in app_settings and chosen on the Settings page; later these will be assigned
per user instead.

Access model: every feature is open to everyone UNLESS it is restricted to a role.
A restricted feature is usable by that role and any higher-ranked role, so:

    Administrator  ⊇  Tester  ⊇  User (unrestricted)

i.e. restricting a feature to Tester lets Testers and Administrators use it;
restricting it to Administrator lets only Administrators use it. (Change `_RANK`
if you'd rather treat the roles as independent rather than nested.)
"""
from __future__ import annotations

from typing import Optional

from . import settings

ADMINISTRATOR = "Administrator"
USER = "User"
TESTER = "Tester"
ROLES = (ADMINISTRATOR, USER, TESTER)   # display order (matches the product list)
DEFAULT_ROLE = ADMINISTRATOR

SETTING_KEY = "active_role"

# Higher rank can use everything a lower rank can, plus features restricted to it.
# Understands BOTH vocabularies: the global system roles (Administrator/User/Tester)
# and the per-user account roles (Admin/Team Manager/User/Tester). "Admin" == the
# Administrator system role. "Team Manager" gates team features (added later), not
# system features, so for feature-gating it ranks as a plain User.
_RANK = {
    "user": 0, "team manager": 0,
    "tester": 1,
    "administrator": 2, "admin": 2,
}


def rank_of(role: Optional[str]) -> int:
    """Feature-gating rank for any role name (case-insensitive). Unknown -> 0, i.e.
    least privilege — RBAC checks fail CLOSED, never escalate on an unexpected value."""
    return _RANK.get((role or "").strip().lower(), 0)


def normalise_role(value: Optional[str]) -> str:
    """Coerce a stored/form value to a known role (case-insensitive), else the default."""
    v = (value or "").strip().lower()
    for r in ROLES:
        if v == r.lower():
            return r
    return DEFAULT_ROLE


def get_role(conn) -> str:
    """The current global active role."""
    return normalise_role(settings.get_setting(conn, SETTING_KEY, DEFAULT_ROLE))


def set_role(conn, value: str) -> None:
    settings.set_setting(conn, SETTING_KEY, normalise_role(value))


def allows(current_role: Optional[str], required_role: Optional[str]) -> bool:
    """Can `current_role` use a feature that requires `required_role`?

    `required_role` of None/"" means the feature is unrestricted (open to everyone).
    Works for both the global system roles and per-user account roles, and fails
    closed on any unrecognised current role.
    """
    if not required_role:
        return True
    return rank_of(current_role) >= rank_of(required_role)
