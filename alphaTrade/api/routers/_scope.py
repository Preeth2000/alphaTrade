"""Shared request-scoping helpers for alphaTrade API routers."""
from __future__ import annotations

from typing import Optional
from fastapi import Request


def scoped_user_id(request: Request) -> Optional[str]:
    """Return the user_id to use as a query filter.

    Admin role has full visibility — returns None so repo methods skip the
    WHERE user_id = ? clause and return all users' data.
    Developer and standard roles are scoped to their own data.
    """
    role = getattr(request.state, "role", None)
    if role == "admin":
        return None
    return getattr(request.state, "user_id", None)
