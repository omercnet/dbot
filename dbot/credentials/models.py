"""Pydantic models for credential profiles."""

from pydantic import BaseModel


class CredentialProfile(BaseModel):
    """Credentials for a single integration pack."""

    pack: str
    params: dict[str, str]
