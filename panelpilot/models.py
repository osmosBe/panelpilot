"""The complete, deliberately small web-editable configuration surface."""
import re
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    dashboard_url: str = "http://homeassistant.local:8123"
    dashboard_brightness: int = Field(default=60, ge=1, le=100)
    photo_brightness: int = Field(default=20, ge=1, le=100)
    idle_seconds: int = Field(default=300, ge=30, le=7200)
    slide_seconds: int = Field(default=30, ge=5, le=3600)
    photo_directory: str = "/srv/panel-photos"
    idle_mode: Literal["photos", "black", "off"] = "photos"
    voice_enabled: bool = True
    auto_rotate: bool = True

    @field_validator("dashboard_url")
    @classmethod
    def valid_url(cls, value):
        if len(value) > 2048 or re.search(r'[\s\x00-\x1f\x7f\\]', value):
            raise ValueError("Die URL enthält unzulässige Zeichen.")
        try:
            url = urlsplit(value)
            port = url.port
            if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
                raise ValueError()
            if port is not None and not 1 <= port <= 65535:
                raise ValueError()
        except ValueError:
            raise ValueError("Bitte eine vollständige HTTP-/HTTPS-Adresse ohne Zugangsdaten eingeben.") from None
        return value

    @field_validator("photo_directory")
    @classmethod
    def valid_path(cls, value):
        if len(value) > 2048 or re.search(r'[\x00-\x1f\x7f]', value):
            raise ValueError("Der Fotopfad enthält unzulässige Zeichen.")
        if not value.startswith("/") or ".." in PurePosixPath(value).parts:
            raise ValueError("Bitte einen absoluten, lokal eingebundenen Ordner angeben.")
        return str(PurePosixPath(value))


class SaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str = Field(min_length=64, max_length=64)
    settings: Settings


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=14, max_length=128)
