# region MODULE_CONTRACT
# PURPOSE: Resolve the stable operator configuration once at the composition root so mock and future publication flows share CLI/environment precedence.
# SCOPE:
# - Immutable command settings, environment fallback, and target validation.
# - NOT: argument parsing, index loading, HTTP, or S3 construction.
# INVARIANTS: An explicit CLI value wins; an empty environment value never replaces a default; each invocation selects one index family.
# KEYWORDS: config, CLI, environment, settings, validation
# endregion MODULE_CONTRACT

"""Configuration values for one independent publication command."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from arduino_mirror.domain import IndexFamily, PinnedPlatform, PinnedTool

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "DEFAULT_ARCHITECTURES",
    "DEFAULT_LIBRARY_INPUT",
    "DEFAULT_MIRROR_HOST",
    "DEFAULT_PACKAGES",
    "DEFAULT_PACKAGE_INPUT",
    "DEFAULT_PINNED_PLATFORMS",
    "DEFAULT_PINNED_TOOLS",
    "DEFAULT_RETRY_ATTEMPTS",
    "DEFAULT_RETRY_BASE_DELAY",
    "Config",
    "TargetKind",
]

DEFAULT_MIRROR_HOST = "https://arduino-downloads.amperka.ru"
DEFAULT_PACKAGE_INPUT = "https://downloads.arduino.cc/packages/package_index.json"
DEFAULT_LIBRARY_INPUT = "https://downloads.arduino.cc/libraries/library_index.json"
DEFAULT_ARCHITECTURES = ("avr", "samd", "sam", "megaavr", "mbed_nano", "mbed_rp2040")
DEFAULT_PACKAGES = ("arduino", "builtin")
DEFAULT_PINNED_TOOLS = (
    PinnedTool("builtin", "ctags", "5.8-arduino11"),
    PinnedTool("builtin", "serial-discovery", "1.0.0"),
)
DEFAULT_PINNED_PLATFORMS: tuple[PinnedPlatform, ...] = ()
DEFAULT_RETRY_ATTEMPTS = 10
_PINNED_TOOL_PATTERN = re.compile(r"([^,:@\s]+):([^,:@\s]+)@([^,:@\s]+)")
_PINNED_PLATFORM_PATTERN = re.compile(r"([^,:@\s]+):([^,:@\s]+)@([^,:@\s]+)")
DEFAULT_RETRY_BASE_DELAY = 1.0


# region CLASS__ConfigSources
# PURPOSE: Centralize CLI and environment precedence so configuration assembly stays declarative.
@dataclass(frozen=True)
class _ConfigSources:
    """Resolve CLI values and environment fallbacks for one command invocation."""

    values: Mapping[str, str | bool | float | int | None]
    environment: Mapping[str, str]

    def setting(self, name: str, env_name: str, default: str) -> str:
        """Return the explicit non-empty CLI value, environment value, or default."""
        value = self.values.get(name)
        if isinstance(value, str) and value:
            return value
        return self.environment.get(env_name) or default

    def csv(
        self, name: str, env_name: str, default: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Return comma-separated values with surrounding whitespace removed."""
        value = self.setting(name, env_name, ",".join(default))
        return tuple(part.strip() for part in value.split(",") if part.strip())

    def pinned_tools(self) -> tuple[PinnedTool, ...]:
        """Return validated, deduplicated exact tool identities."""
        raw = self.setting(
            "pinned_tools",
            "PINNED_TOOLS",
            ",".join(tool.identity for tool in DEFAULT_PINNED_TOOLS),
        )
        parts = raw.split(",")
        if any(not part.strip() for part in parts):
            msg = (
                "pinned tools must be comma-separated packager:name@version identities"
            )
            raise ValueError(msg)
        tools: list[PinnedTool] = []
        for part in parts:
            match = _PINNED_TOOL_PATTERN.fullmatch(part.strip())
            if match is None:
                msg = "pinned tools must be comma-separated packager:name@version identities"
                raise ValueError(msg)
            tools.append(PinnedTool(*match.groups()))
        return tuple(dict.fromkeys(tools))

    def pinned_platforms(self) -> tuple[PinnedPlatform, ...]:
        """Return validated, deduplicated exact platform identities."""
        raw = self.setting("pinned_platforms", "PINNED_PLATFORMS", "")
        if not raw:
            return DEFAULT_PINNED_PLATFORMS
        parts = raw.split(",")
        if any(not part.strip() for part in parts):
            msg = "pinned platforms must be comma-separated packager:architecture@version identities"
            raise ValueError(msg)
        platforms: list[PinnedPlatform] = []
        for part in parts:
            match = _PINNED_PLATFORM_PATTERN.fullmatch(part.strip())
            if match is None:
                msg = "pinned platforms must be comma-separated packager:architecture@version identities"
                raise ValueError(msg)
            platforms.append(PinnedPlatform(*match.groups()))
        return tuple(dict.fromkeys(platforms))

    def retry_attempts(self) -> int:
        """Return a validated retry-attempt count."""
        value = self.values.get("retry_attempts")
        if isinstance(value, int) and not isinstance(value, bool):
            parsed = value
        else:
            raw = self.environment.get("RETRY_ATTEMPTS")
            parsed = int(raw) if raw else DEFAULT_RETRY_ATTEMPTS
        if parsed < 1:
            msg = "retry attempts must be a positive integer"
            raise ValueError(msg)
        return parsed

    def retry_base_delay(self) -> float:
        """Return a validated retry base delay."""
        value = self.values.get("retry_base_delay")
        if isinstance(value, float):
            parsed = value
        else:
            raw = self.environment.get("RETRY_BASE_DELAY")
            parsed = float(raw) if raw else DEFAULT_RETRY_BASE_DELAY
        if parsed < 0:
            msg = "retry base delay must be non-negative"
            raise ValueError(msg)
        return parsed

    def dry_run(self) -> bool:
        """Return the explicit CLI flag or its environment fallback."""
        value = self.values.get("dry_run")
        return (
            value
            if isinstance(value, bool)
            else self.environment.get("DRY_RUN", "").lower() in {"1", "true", "yes"}
        )

    def local_index(self, environment_name: str) -> Path | None:
        """Return the configured family-local overlay path, if any."""
        value = self.values.get("local_index")
        if isinstance(value, str) and value:
            return Path(value)
        raw = self.environment.get(environment_name)
        return Path(raw) if raw else None


# endregion CLASS__ConfigSources


# region FUNC__family_index_settings
# PURPOSE: Keep family-specific index defaults and environment names consistent.
def _family_index_settings(family: IndexFamily) -> tuple[str, str, str]:
    """Return the input and overlay environment names and default URL for one family."""
    if family is IndexFamily.PACKAGES:
        return (
            "PACKAGES_INPUT_INDEX",
            DEFAULT_PACKAGE_INPUT,
            "PACKAGES_LOCAL_INDEX",
        )
    return (
        "LIBRARIES_INPUT_INDEX",
        DEFAULT_LIBRARY_INPUT,
        "LIBRARIES_LOCAL_INDEX",
    )


# endregion FUNC__family_index_settings


# region CLASS_TargetKind
# PURPOSE: Restrict publication configuration to storage targets that the composition root can build.
class TargetKind(StrEnum):
    """Supported publication storage targets."""

    LOCAL = "local"
    S3 = "s3"


# endregion CLASS_TargetKind


# region CLASS_Config
# PURPOSE: Give the composition root one validated immutable value object for a single family publication command.
@dataclass(frozen=True)
class Config:
    """Resolved settings for one package or library publication."""

    family: IndexFamily
    input_index: str
    mirror_host: str
    target: TargetKind
    bucket: str
    prefix: str
    endpoint: str
    region: str
    local_root: Path
    dry_run: bool
    access_key: str
    secret_key: str
    architectures: tuple[str, ...]
    package_names: tuple[str, ...]
    retry_attempts: int
    retry_base_delay: float
    pinned_tools: tuple[PinnedTool, ...] = ()
    pinned_platforms: tuple[PinnedPlatform, ...] = ()
    local_index: Path | None = None

    # region METHOD_index_key
    # PURPOSE: Place the configured source index path in its family's archive namespace.
    @property
    def index_key(self) -> str:
        """Return the validated family-prefixed target key for the configured index."""
        parsed = urlsplit(self.input_index)
        path = parsed.path
        parts = path.lstrip("/").split("/")
        if (
            not parsed.scheme
            or not parsed.netloc
            or not path.startswith("/")
            or not path.strip("/")
            or any(part in {"", ".", ".."} for part in parts)
        ):
            msg = "input index must have a non-empty absolute URL path"
            raise ValueError(msg)
        return f"{self.family.archive_prefix}/{'/'.join(parts)}"

    # endregion METHOD_index_key

    # region METHOD_validate
    # PURPOSE: Reject incomplete target settings before a publication adapter could perform a partial publication.
    def validate(self) -> None:
        """Validate the selected target's required settings."""
        if not self.index_key:
            msg = "input index must have a non-empty absolute URL path"
            raise ValueError(msg)
        match self.target:
            case TargetKind.S3:
                missing = [
                    name
                    for name, value in (
                        ("bucket", self.bucket),
                        ("access key", self.access_key),
                        ("secret key", self.secret_key),
                    )
                    if not value
                ]
                if missing:
                    msg = f"s3 target requires {', '.join(missing)}"
                    raise ValueError(msg)
            case TargetKind.LOCAL:
                if not str(self.local_root):
                    msg = "local target requires local root"
                    raise ValueError(msg)

    # endregion METHOD_validate

    # region METHOD_from_values
    # PURPOSE: Apply CLI → non-empty environment → default precedence without exposing environment access to inner layers.
    @classmethod
    def from_values(
        cls,
        *,
        family: IndexFamily,
        values: Mapping[str, str | bool | float | int | None],
        environment: Mapping[str, str],
    ) -> Config:
        """Resolve command settings from parser values and a supplied environment mapping."""
        sources = _ConfigSources(values, environment)
        input_environment, input_default, local_index_environment = (
            _family_index_settings(family)
        )
        return cls(
            family=family,
            input_index=sources.setting(
                "input_index", input_environment, input_default
            ),
            mirror_host=sources.setting(
                "mirror_host", "MIRROR_HOST", DEFAULT_MIRROR_HOST
            ),
            target=TargetKind(sources.setting("target", "TARGET_KIND", "s3")),
            bucket=sources.setting("bucket", "TARGET_BUCKET", ""),
            prefix=sources.setting("prefix", "TARGET_PREFIX", ""),
            endpoint=sources.setting("endpoint", "TARGET_ENDPOINT", ""),
            region=sources.setting("region", "TARGET_REGION", ""),
            local_root=Path(
                sources.setting("local_root", "TARGET_LOCAL_ROOT", "mirror-out")
            ),
            dry_run=sources.dry_run(),
            access_key=sources.setting("access_key", "AWS_ACCESS_KEY_ID", ""),
            secret_key=sources.setting("secret_key", "AWS_SECRET_ACCESS_KEY", ""),
            architectures=sources.csv(
                "architectures", "ARCHITECTURES", DEFAULT_ARCHITECTURES
            ),
            package_names=sources.csv("package_names", "PACKAGES", DEFAULT_PACKAGES),
            retry_attempts=sources.retry_attempts(),
            retry_base_delay=sources.retry_base_delay(),
            pinned_tools=(
                sources.pinned_tools() if family is IndexFamily.PACKAGES else ()
            ),
            pinned_platforms=(
                sources.pinned_platforms() if family is IndexFamily.PACKAGES else ()
            ),
            local_index=sources.local_index(local_index_environment),
        )

    # endregion METHOD_from_values


# endregion CLASS_Config
