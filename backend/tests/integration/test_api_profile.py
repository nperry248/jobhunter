"""
tests/integration/test_api_profile.py — API tests for GET/PUT /api/v1/profile.

WHAT WE'RE TESTING:
  - GET /profile: auto-creates an empty profile on first access
  - GET /profile: returns existing profile on subsequent calls
  - PUT /profile: saves all fields and returns updated profile
  - PUT /profile: JSON list fields (target_locations, company_blocklist) round-trip correctly
  - PUT /profile: null optional fields are accepted
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.user_profile import UserProfile


class TestGetProfile:
    async def test_get_creates_empty_profile_on_first_access(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        GET /profile when no profile exists should auto-create and return one.
        The response must be 200 with an id and empty-string defaults for name/email.
        """
        response = await client.get("/api/v1/profile")
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["full_name"] == ""
        assert data["email"] == ""
        assert data["target_internships"] is True
        assert data["target_new_grad"] is True
        assert data["target_locations"] == []
        assert data["company_blocklist"] == []

    async def test_get_returns_existing_profile(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        GET /profile when a profile already exists should return it, not create a new one.
        """
        profile = UserProfile(full_name="Nick Perry", email="nick@example.com")
        db_session.add(profile)
        await db_session.flush()

        response = await client.get("/api/v1/profile")
        assert response.status_code == 200
        data = response.json()
        assert data["full_name"] == "Nick Perry"
        assert data["email"] == "nick@example.com"

    async def test_get_parses_json_list_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        JSON-encoded list fields stored in the DB should be returned as real lists.
        """
        import json
        profile = UserProfile(
            full_name="Nick",
            email="nick@example.com",
            target_locations=json.dumps(["San Francisco", "Remote"]),
            company_blocklist=json.dumps(["Bad Corp"]),
        )
        db_session.add(profile)
        await db_session.flush()

        response = await client.get("/api/v1/profile")
        assert response.status_code == 200
        data = response.json()
        assert data["target_locations"] == ["San Francisco", "Remote"]
        assert data["company_blocklist"] == ["Bad Corp"]


class TestUpdateProfile:
    async def test_put_saves_all_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        PUT /profile with a full payload should save every field and return 200.
        """
        payload = {
            "full_name": "Nick Perry",
            "email": "nick@example.com",
            "phone": "+1 555 000 0000",
            "linkedin_url": "https://linkedin.com/in/nick",
            "github_url": "https://github.com/nick",
            "portfolio_url": "https://nick.dev",
            "location": "San Francisco, CA",
            "resume_path": "/data/resumes/nick.pdf",
            "target_internships": True,
            "target_new_grad": False,
            "auto_apply_threshold": 75,
            "target_locations": ["San Francisco", "Remote"],
            "company_blocklist": ["Bad Corp Inc"],
        }
        response = await client.put("/api/v1/profile", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["full_name"] == "Nick Perry"
        assert data["email"] == "nick@example.com"
        assert data["phone"] == "+1 555 000 0000"
        assert data["linkedin_url"] == "https://linkedin.com/in/nick"
        assert data["github_url"] == "https://github.com/nick"
        assert data["portfolio_url"] == "https://nick.dev"
        assert data["location"] == "San Francisco, CA"
        assert data["resume_path"] == "/data/resumes/nick.pdf"
        assert data["target_internships"] is True
        assert data["target_new_grad"] is False
        assert data["auto_apply_threshold"] == 75
        assert data["target_locations"] == ["San Francisco", "Remote"]
        assert data["company_blocklist"] == ["Bad Corp Inc"]

    async def test_put_creates_profile_if_none_exists(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        PUT /profile when no profile exists should create one (not 404).
        """
        response = await client.put("/api/v1/profile", json={"full_name": "Nick", "email": "n@n.com"})
        assert response.status_code == 200
        assert response.json()["full_name"] == "Nick"

    async def test_put_updates_existing_profile(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        PUT /profile when a profile exists should update it in place (not create a second row).
        """
        profile = UserProfile(full_name="Old Name", email="old@example.com")
        db_session.add(profile)
        await db_session.flush()

        response = await client.put(
            "/api/v1/profile",
            json={"full_name": "New Name", "email": "new@example.com"},
        )
        assert response.status_code == 200
        assert response.json()["full_name"] == "New Name"
        assert response.json()["email"] == "new@example.com"

    async def test_put_null_optional_fields_accepted(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        Optional fields (phone, linkedin_url, etc.) can be omitted or null.
        """
        response = await client.put(
            "/api/v1/profile",
            json={"full_name": "Nick", "email": "nick@example.com"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["phone"] is None
        assert data["linkedin_url"] is None
        assert data["auto_apply_threshold"] is None

    async def test_put_empty_list_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        Empty lists for target_locations and company_blocklist should save and return as [].
        """
        response = await client.put(
            "/api/v1/profile",
            json={
                "full_name": "Nick",
                "email": "nick@example.com",
                "target_locations": [],
                "company_blocklist": [],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["target_locations"] == []
        assert data["company_blocklist"] == []

    async def test_get_after_put_returns_updated_data(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        After a PUT, a subsequent GET should return the same updated data.
        This verifies the data was actually persisted, not just returned from memory.
        """
        await client.put(
            "/api/v1/profile",
            json={"full_name": "Nick Perry", "email": "nick@example.com", "location": "Austin, TX"},
        )
        response = await client.get("/api/v1/profile")
        assert response.status_code == 200
        assert response.json()["location"] == "Austin, TX"
