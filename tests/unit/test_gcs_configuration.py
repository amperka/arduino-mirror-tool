# region MODULE_CONTRACT
# PURPOSE: Keep GCS selection additive and production WIF workflows independent of legacy secrets.
# SCOPE: CLI/environment precedence, validation, composition, and static workflow contracts.
# KEYWORDS: GCS, configuration, WIF, GitHub Actions
# endregion MODULE_CONTRACT

"""Configuration and deployment contracts for GCS publication."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from arduino_mirror.domain import IndexFamily
from arduino_mirror.entrypoints.cli import _build_parser
from arduino_mirror.entrypoints.config import Config, TargetKind
from arduino_mirror.entrypoints.di import make_publication_use_case
from arduino_mirror.infra import RetryPolicy


# region FUNC_test_gcs_configuration_uses_cli_and_environment_without_s3_keys
# PURPOSE: Verify both supported configuration paths select GCS without requiring legacy credentials.
@pytest.mark.parametrize("family", list(IndexFamily))
def test_gcs_configuration_uses_cli_and_environment_without_s3_keys(family) -> None:
    args = _build_parser().parse_args(
        [family.value, "--target", "gcs", "--bucket", "cli-bucket"]
    )
    environment = {"TARGET_KIND": "gcs", "TARGET_BUCKET": "environment-bucket"}
    explicit = Config.from_values(
        family=family, values=vars(args), environment=environment
    )
    fallback = Config.from_values(family=family, values={}, environment=environment)
    explicit.validate()
    fallback.validate()
    assert explicit.target is fallback.target is TargetKind.GCS
    assert explicit.bucket == "cli-bucket"
    assert fallback.bucket == "environment-bucket"
    assert explicit.access_key == explicit.secret_key == ""
    assert (
        Config.from_values(family=family, values={}, environment={}).target
        is TargetKind.S3
    )


# endregion FUNC_test_gcs_configuration_uses_cli_and_environment_without_s3_keys


# region FUNC_test_gcs_configuration_requires_bucket
# PURPOSE: Fail before composition when a GCS destination is missing.
def test_gcs_configuration_requires_bucket() -> None:
    config = Config.from_values(
        family=IndexFamily.PACKAGES, values={"target": "gcs"}, environment={}
    )
    with pytest.raises(ValueError, match="gcs target requires bucket"):
        config.validate()


# endregion FUNC_test_gcs_configuration_requires_bucket


# region FUNC_test_gcs_composition_forwards_only_storage_neutral_settings
# PURPOSE: Keep legacy endpoint and S3 keys outside GCS composition even if old environment values remain.
def test_gcs_composition_forwards_only_storage_neutral_settings(monkeypatch) -> None:
    constructor = Mock()
    monkeypatch.setattr(
        "arduino_mirror.entrypoints.di.GcsPublicationTarget", constructor
    )
    config = Config.from_values(
        family=IndexFamily.LIBRARIES,
        values={
            "target": "gcs",
            "bucket": "mirror",
            "prefix": "managed",
            "retry_attempts": 3,
            "retry_base_delay": 0.0,
        },
        environment={
            "AWS_ACCESS_KEY_ID": "obsolete",
            "AWS_SECRET_ACCESS_KEY": "obsolete",
            "TARGET_ENDPOINT": "https://old.invalid",
        },
    )
    config.validate()
    make_publication_use_case(config)
    constructor.assert_called_once_with(
        bucket="mirror",
        index_key="l/libraries/library_index.json",
        prefix="managed",
        retry_policy=RetryPolicy(max_attempts=3, base_delay=0.0),
    )


# endregion FUNC_test_gcs_composition_forwards_only_storage_neutral_settings


# region FUNC_test_production_workflows_use_only_wif_secrets
# PURPOSE: Prevent stale repository secrets from redirecting either independent production family workflow to S3.
@pytest.mark.parametrize("family", ["packages", "libraries"])
def test_production_workflows_use_only_wif_secrets(family) -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / ".github" / "workflows" / f"mirror-{family}.yml").read_text(
        encoding="utf-8"
    )
    workflow = yaml.safe_load(text)
    assert workflow["permissions"] == {"contents": "read", "id-token": "write"}
    assert workflow["env"]["TARGET_KIND"] == "gcs"
    assert workflow["env"]["TARGET_BUCKET"] == "amperka-arduino-downloads"
    assert workflow["env"]["TARGET_PREFIX"] == ""
    job = workflow["jobs"]["mirror"]
    assert job["environment"] == "prod"
    steps = job["steps"]
    auth_index = next(
        i
        for i, step in enumerate(steps)
        if step.get("uses", "").startswith("google-github-actions/auth@")
    )
    assert auth_index > next(
        i
        for i, step in enumerate(steps)
        if step.get("uses", "").startswith("actions/checkout@")
    )
    auth = steps[auth_index]
    assert auth["uses"] == "google-github-actions/auth@v3"
    assert auth["with"] == {
        "workload_identity_provider": "${{ secrets.GCP_WIF_PROVIDER }}",
        "service_account": "${{ secrets.GCP_WIF_SERVICE_ACCOUNT }}",
        "create_credentials_file": True,
        "export_environment_variables": True,
    }
    assert re.findall(r"secrets\.([A-Z_]+)", text) == [
        "GCP_WIF_PROVIDER",
        "GCP_WIF_SERVICE_ACCOUNT",
    ]
    assert [
        step["run"]
        for step in steps
        if step.get("run", "").startswith("uv run arduino-mirror")
    ] == [f"uv run arduino-mirror {family}"]
    assert steps[-1]["run"] == f"uv run arduino-mirror {family}"
    publish_environment = {"MIRROR_HOST": "${{ vars.MIRROR_HOST }}"}
    if family == "packages":
        publish_environment.update(
            {
                "ARCHITECTURES": "${{ vars.ARCHITECTURES }}",
                "PACKAGES": "${{ vars.PACKAGES }}",
                "PACKAGES_LOCAL_INDEX": "data/package_index_overlay.json",
                "PINNED_TOOLS": "${{ vars.PINNED_TOOLS }}",
                "PINNED_PLATFORMS": "${{ vars.PINNED_PLATFORMS }}",
            }
        )
    assert steps[-1]["env"] == publish_environment
    assert workflow["concurrency"]["group"] == f"mirror-{family}-publication"


# endregion FUNC_test_production_workflows_use_only_wif_secrets
