"""Application configuration.

Configuration is environment-driven (see ``.env.example``). Two prefixes exist:

* ``PAPERLESS_*``  - how to reach the Paperless-ngx instance we orchestrate.
* ``PAPERWRENCH_*`` - PaperWrench's own behaviour.

Legacy shared-token fields remain parseable for downgrade compatibility but
normal operation uses per-user server-side sessions. Tokens must never be
persisted to SQLite, returned by an endpoint, or logged. See ADR-0016.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated
from typing import Literal

from pydantic import Field
from pydantic import SecretStr
from pydantic import field_validator
from pydantic import model_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

# Paperless API version PaperWrench is developed and tested against.
#
# Verified for Paperless-ngx 3.1.2: ALLOWED_VERSIONS = ["9", "10"],
# DEFAULT_VERSION = "10". We negotiate explicitly rather than relying on the
# server default, which will move in future releases. See ADR-0003.
SUPPORTED_PAPERLESS_API_VERSION = 10
MIN_PAPERLESS_API_VERSION = 9

# Hard ceiling on write concurrency. Users must not be able to accidentally
# DoS their own Paperless instance from a config file.
MAX_ALLOWED_CONCURRENCY = 16

# `vite build` writes here (see frontend/vite.config.ts), so the single
# container image ships the SPA inside the Python package.
PACKAGED_STATIC_DIR = Path(__file__).resolve().parent / "static"


class Settings(BaseSettings):
    """Environment-driven settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- Paperless connection ------------------------------------------------
    paperless_url: str = Field(
        default="",
        validation_alias="PAPERLESS_URL",
        description="Base URL of the Paperless-ngx instance, e.g. http://paperless:8000",
    )
    paperless_token: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="PAPERLESS_TOKEN",
        description=(
            "Paperless API token. Never logged, never persisted, never sent to the browser."
        ),
    )
    paperless_token_file: Path | None = Field(
        default=None,
        validation_alias="PAPERLESS_TOKEN_FILE",
        description="Path to a file containing the token (Docker secrets).",
    )
    paperless_api_version: int = Field(
        default=SUPPORTED_PAPERLESS_API_VERSION,
        validation_alias="PAPERWRENCH_PAPERLESS_API_VERSION",
        ge=MIN_PAPERLESS_API_VERSION,
    )
    paperless_verify_ssl: bool = Field(
        default=True,
        validation_alias="PAPERWRENCH_PAPERLESS_VERIFY_SSL",
    )
    paperless_timeout_connect: float = Field(
        default=10.0, validation_alias="PAPERWRENCH_PAPERLESS_TIMEOUT_CONNECT", gt=0
    )
    paperless_timeout_read: float = Field(
        default=60.0, validation_alias="PAPERWRENCH_PAPERLESS_TIMEOUT_READ", gt=0
    )

    # -- PaperWrench behaviour ----------------------------------------------
    database_url: str = Field(
        default="sqlite+pysqlite:///./data/paperwrench.db",
        validation_alias="PAPERWRENCH_DATABASE_URL",
    )
    host: str = Field(default="0.0.0.0", validation_alias="PAPERWRENCH_HOST")  # noqa: S104
    port: int = Field(default=8000, validation_alias="PAPERWRENCH_PORT", ge=1, le=65535)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO", validation_alias="PAPERWRENCH_LOG_LEVEL"
    )
    log_format: Literal["json", "console"] = Field(
        default="json", validation_alias="PAPERWRENCH_LOG_FORMAT"
    )

    default_page_size: Annotated[int, Field(ge=1, le=250)] = Field(
        default=100, validation_alias="PAPERWRENCH_DEFAULT_PAGE_SIZE"
    )
    max_concurrency: Annotated[int, Field(ge=1, le=MAX_ALLOWED_CONCURRENCY)] = Field(
        default=4,
        validation_alias="PAPERWRENCH_MAX_CONCURRENCY",
        description="Bounded concurrency for Paperless writes. See ADR-0006.",
    )

    auto_resume_jobs: bool = Field(
        default=False,
        validation_alias="PAPERWRENCH_AUTO_RESUME_JOBS",
        description="Reserved legacy setting. M8 requires explicit resume and refuses true.",
    )

    # Split-origin development only. Empty in production (single origin).
    cors_origins: list[str] = Field(
        default_factory=list, validation_alias="PAPERWRENCH_CORS_ORIGINS"
    )
    session_ttl_seconds: Annotated[int, Field(ge=300, le=604800)] = Field(
        default=28800,
        validation_alias="PAPERWRENCH_SESSION_TTL_SECONDS",
        description="Absolute login-session lifetime; defaults to eight hours.",
    )
    session_revalidate_seconds: Annotated[int, Field(ge=0, le=3600)] = Field(
        default=300,
        validation_alias="PAPERWRENCH_SESSION_REVALIDATE_SECONDS",
        description="How often Paperless token revocation is checked.",
    )
    session_cookie_secure: bool | None = Field(
        default=None,
        validation_alias="PAPERWRENCH_SESSION_COOKIE_SECURE",
        description="Force Secure cookies; unset selects it from the request scheme.",
    )

    # Directory containing the built SPA. Defaults to the packaged
    # `paperwrench/static` produced by `vite build`, which is what the
    # single-container image ships (ADR-0001). Absent in local dev, where the
    # Vite dev server serves the SPA instead.
    static_dir: Path | None = Field(default=None, validation_alias="PAPERWRENCH_STATIC_DIR")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("paperless_url")
    @classmethod
    def _normalise_url(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _load_token_file(self) -> Settings:
        """Support Docker secrets: PAPERLESS_TOKEN_FILE wins over the inline env var."""
        if self.paperless_token_file is not None and self.paperless_token_file.is_file():
            token = self.paperless_token_file.read_text(encoding="utf-8").strip()
            if token:
                object.__setattr__(self, "paperless_token", SecretStr(token))
        return self

    # -- Derived -------------------------------------------------------------
    @field_validator("auto_resume_jobs")
    @classmethod
    def _manual_resume_only(cls, value: bool) -> bool:
        if value:
            raise ValueError("M8 requires explicit resume; automatic resume is unsupported")
        return value

    @property
    def paperless_configured(self) -> bool:
        """True when the shared-token legacy configuration is complete."""
        return bool(self.paperless_url) and bool(self.paperless_token.get_secret_value())

    @property
    def paperless_login_configured(self) -> bool:
        """A URL is sufficient for per-user token login."""
        return bool(self.paperless_url)

    @property
    def accept_header(self) -> str:
        """Explicit API version negotiation header (ADR-0003)."""
        return f"application/json; version={self.paperless_api_version}"

    @property
    def sqlite_path(self) -> Path | None:
        """Filesystem path of the SQLite database, when the URL is a local file."""
        prefix = "sqlite+pysqlite:///"
        if self.database_url.startswith(prefix):
            raw = self.database_url[len(prefix) :]
            if raw and raw != ":memory:":
                return Path(raw)
        return None

    @property
    def resolved_static_dir(self) -> Path | None:
        """Where the SPA is served from, if anywhere.

        An explicit `PAPERWRENCH_STATIC_DIR` always wins; otherwise the
        packaged build directory is used when it actually contains a build.
        """
        if self.static_dir is not None:
            return self.static_dir if self.static_dir.is_dir() else None
        packaged = PACKAGED_STATIC_DIR
        return packaged if (packaged / "index.html").is_file() else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
