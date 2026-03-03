# Makes `models/` a Python package.
#
# IMPORTANT: Import ALL models here so Alembic can discover them during
# `alembic revision --autogenerate`. If a model isn't imported here
# (directly or transitively), Alembic won't see it and won't generate its migration.
from models.application import Application
from models.job import Job
from models.user_profile import UserProfile

__all__ = ["Job", "Application", "UserProfile"]
