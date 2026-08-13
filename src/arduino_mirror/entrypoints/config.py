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

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from arduino_mirror.domain import IndexFamily

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "DEFAULT_ARCHITECTURES",
    "DEFAULT_LIBRARY_INPUT",
    "DEFAULT_MIRROR_HOST",
    "DEFAULT_PACKAGES",
    "DEFAULT_PACKAGE_INPUT",
    "Config",
]

DEFAULT_MIRROR_HOST = "https://arduino-downloads.amperka.ru"
DEFAULT_PACKAGE_INPUT = "https://downloads.arduino.cc/packages/package_index.json"
DEFAULT_LIBRARY_INPUT = "https://downloads.arduino.cc/libraries/library_index.json"
DEFAULT_ARCHITECTURES = ("avr", "samd", "sam", "megaavr", "mbed_nano", "mbed_rp2040")
DEFAULT_PACKAGES = ("arduino", "builtin")


# region CLASS_Config
# PURPOSE: Give the composition root one validated immutable value object for a single family publication command.
@dataclass(frozen=True)
class Config:
    """Resolved settings for one package or library publication."""

    family: IndexFamily
    input_index: str
    mirror_host: str
    target: str
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

    # region METHOD_validate
    # PURPOSE: Reject incomplete target settings before a publication adapter could perform a partial publication.
    def validate(self) -> None:
        """Validate the selected target's required settings."""
        if self.target == "s3":
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
        elif self.target == "local":
            if not str(self.local_root):
                msg = "local target requires local root"
                raise ValueError(msg)
        else:
            msg = f"unknown target: {self.target}"
            raise ValueError(msg)

    # endregion METHOD_validate

    # region METHOD_from_values
    # PURPOSE: Apply CLI → non-empty environment → default precedence without exposing environment access to inner layers.
    @classmethod
    def from_values(
        cls,
        *,
        family: IndexFamily,
        values: Mapping[str, str | bool | None],
        environment: Mapping[str, str],
    ) -> Config:
        """Resolve command settings from parser values and a supplied environment mapping."""

        def setting(name: str, env_name: str, default: str) -> str:
            value = values.get(name)
            if isinstance(value, str) and value:
                return value
            return environment.get(env_name) or default

        def csv(name: str, env_name: str, default: tuple[str, ...]) -> tuple[str, ...]:
            value = setting(name, env_name, ",".join(default))
            return tuple(part.strip() for part in value.split(",") if part.strip())

        dry_value = values.get("dry_run")
        dry_run = (
            dry_value
            if isinstance(dry_value, bool)
            else (environment.get("DRY_RUN", "").lower() in {"1", "true", "yes"})
        )
        input_env = (
            "PACKAGES_INPUT_INDEX"
            if family is IndexFamily.PACKAGES
            else "LIBRARIES_INPUT_INDEX"
        )
        input_default = (
            DEFAULT_PACKAGE_INPUT
            if family is IndexFamily.PACKAGES
            else DEFAULT_LIBRARY_INPUT
        )
        return cls(
            family=family,
            input_index=setting("input_index", input_env, input_default),
            mirror_host=setting("mirror_host", "MIRROR_HOST", DEFAULT_MIRROR_HOST),
            target=setting("target", "TARGET_KIND", "s3"),
            bucket=setting("bucket", "TARGET_BUCKET", ""),
            prefix=setting("prefix", "TARGET_PREFIX", ""),
            endpoint=setting("endpoint", "TARGET_ENDPOINT", ""),
            region=setting("region", "TARGET_REGION", ""),
            local_root=Path(setting("local_root", "TARGET_LOCAL_ROOT", "mirror-out")),
            dry_run=dry_run,
            access_key=setting("access_key", "AWS_ACCESS_KEY_ID", ""),
            secret_key=setting("secret_key", "AWS_SECRET_ACCESS_KEY", ""),
            architectures=csv("architectures", "ARCHITECTURES", DEFAULT_ARCHITECTURES),
            package_names=csv("package_names", "PACKAGES", DEFAULT_PACKAGES),
        )

    # endregion METHOD_from_values


# endregion CLASS_Config
