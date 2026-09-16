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
_RANK = {USER: 0, TESTER: 1, ADMINISTRATOR: 2}


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
    """
    if not required_role:
        return True
    return _RANK.get(normalise_role(current_role), 0) >= _RANK.get(normalise_role(required_role), 99)
