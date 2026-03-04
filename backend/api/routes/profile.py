"""
api/routes/profile.py — HTTP endpoints for the user's profile.

ENDPOINTS:
  GET  /api/v1/profile  — Return the user's profile (auto-creates if none exists)
  PUT  /api/v1/profile  — Save all profile fields (full replace)

DESIGN DECISIONS:

  Single-row table:
    This is a single-user system. There is exactly ONE UserProfile row.
    GET auto-creates an empty profile on first access so the frontend never
    gets a 404 on load — it always gets a profile back (possibly empty).

  PUT not PATCH:
    The Settings page always sends every field (the full form). A full
    PUT (replace) is simpler than a PATCH (partial update) here because
    we don't need to merge anything — we just overwrite all fields.

  JSON fields as Python lists:
    target_locations and company_blocklist are stored as JSON strings in
    the DB (e.g. '["San Francisco", "Remote"]') but exposed as list[str]
    in the API. The route handler converts between the two.
    WHY JSON strings in the DB: avoids extra join tables for small lists.
"""

import json
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from models.user_profile import UserProfile

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class ProfileResponse(BaseModel):
    """Shape of the profile object returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: str
    phone: str | None
    linkedin_url: str | None
    github_url: str | None
    portfolio_url: str | None
    location: str | None
    resume_path: str | None
    target_internships: bool
    target_new_grad: bool
    auto_apply_threshold: int | None
    target_locations: list[str]
    company_blocklist: list[str]

    @field_validator("target_locations", "company_blocklist", mode="before")
    @classmethod
    def parse_json_list(cls, value: str | list | None) -> list[str]:
        """
        Convert JSON-encoded strings from the DB to Python lists.

        The DB stores these as strings: '["SF", "Remote"]'
        The API exposes them as lists: ["SF", "Remote"]

        If the value is already a list (e.g. from a test), pass through unchanged.
        """
        if value is None:
            return []
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return []
        return value


class ProfileUpdateRequest(BaseModel):
    """
    Request body for PUT /profile.

    All fields have defaults so partial payloads don't explode,
    but the Settings UI always sends every field.
    """
    full_name: str = ""
    email: str = ""
    phone: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    location: str | None = None
    resume_path: str | None = None
    target_internships: bool = True
    target_new_grad: bool = True
    auto_apply_threshold: int | None = None
    target_locations: list[str] = []
    company_blocklist: list[str] = []


# ── GET /api/v1/profile ───────────────────────────────────────────────────────

@router.get("", response_model=ProfileResponse)
async def get_profile(db: AsyncSession = Depends(get_db)) -> ProfileResponse:
    """
    Return the user's profile. Auto-creates an empty one if none exists.

    WHY AUTO-CREATE:
      On first launch there are no rows in user_profiles. Rather than
      returning a 404 (which the frontend has to handle as a special case),
      we just create and return an empty profile. The user then fills it in
      via the Settings page and saves.
    """
    result = await db.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = UserProfile()
        db.add(profile)
        await db.flush()  # assigns the UUID, doesn't commit yet

    return ProfileResponse.model_validate(profile)


# ── PUT /api/v1/profile ───────────────────────────────────────────────────────

@router.put("", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> ProfileResponse:
    """
    Save all profile fields. Creates the profile if it doesn't exist yet.

    Returns the updated profile so the frontend can confirm what was saved.
    """
    result = await db.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = UserProfile()
        db.add(profile)

    # Apply all fields from the request body.
    # JSON-serializable list fields are stored as JSON strings in the DB.
    profile.full_name = body.full_name
    profile.email = body.email
    profile.phone = body.phone
    profile.linkedin_url = body.linkedin_url
    profile.github_url = body.github_url
    profile.portfolio_url = body.portfolio_url
    profile.location = body.location
    profile.resume_path = body.resume_path
    profile.target_internships = body.target_internships
    profile.target_new_grad = body.target_new_grad
    profile.auto_apply_threshold = body.auto_apply_threshold
    profile.target_locations = json.dumps(body.target_locations)
    profile.company_blocklist = json.dumps(body.company_blocklist)

    await db.flush()

    return ProfileResponse.model_validate(profile)
