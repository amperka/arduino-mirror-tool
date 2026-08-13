# region MODULE_CONTRACT
# PURPOSE: Prove fixture-backed test doubles preserve independent publication, transformation, and configuration contracts.
# SCOPE:
# - Pure selection, transformed indexes, recorded boundaries, failure isolation, configuration precedence, dry run, and trace records.
# - NOT: Real HTTP, filesystem, or S3 interactions.
# KEYWORDS: unit test, test double, publication, selection, configuration, logging
# endregion MODULE_CONTRACT

"""Tests for fixture-backed publication selection and orchestration."""

from __future__ import annotations

import json
import logging
import signal
from pathlib import Path

import pytest

from arduino_mirror.application import (
    LatestLibrariesPolicy,
    LatestPackagesPolicy,
    PublishFamily,
)
from arduino_mirror.domain import IndexFamily
from arduino_mirror.entrypoints.config import Config
from arduino_mirror.entrypoints.signals import PublicationCancelledError
from tests.doubles import (
    FixtureIndexSource,
    RecordingPublicationTarget,
    TargetArchiveError,
)
from tests.log_assertions import extra_fields

_AVR_ARCHIVE_SIZE = 7_162_150
_ROOT = Path(__file__).resolve().parents[2]
_SIGTERM_EXIT = 128 + int(signal.SIGTERM)


def run_fixture_flow(
    config: Config, *, present_keys: tuple[str, ...] = (), fail_archives: bool = False
):
    """Run fixture data through the application use case and test doubles."""
    path = (
        _ROOT / "tests" / "fixtures" / "package_index.json"
        if config.family is IndexFamily.PACKAGES
        else _ROOT / "tests" / "fixtures" / "library_index.json"
    )
    origin = (
        "http://127.0.0.1:18099"
        if config.family is IndexFamily.PACKAGES
        else "http://fixture.arduino.test"
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    selection = (
        LatestPackagesPolicy(
            mirror_host=config.mirror_host,
            origin_host=origin,
            architectures=config.architectures,
            package_names=config.package_names,
        )
        if config.family is IndexFamily.PACKAGES
        else LatestLibrariesPolicy(mirror_host=config.mirror_host, origin_host=origin)
    )
    target = RecordingPublicationTarget(
        present_keys=present_keys, fail_archives=fail_archives
    )
    use_case = PublishFamily(
        source=FixtureIndexSource(family=config.family, raw_index=raw),
        selection=selection,
        target=target,
    )
    plan = (
        use_case.preview(config.family)
        if config.dry_run
        else use_case.run(config.family)
    )
    return plan, target


# region FUNC_make_config
# PURPOSE: Create explicit local-target settings so tests isolate pure test double behavior from process environment.
def make_config(
    family: IndexFamily,
    *,
    dry_run: bool = False,
    mirror_host: str = "https://mirror.test.invalid",
) -> Config:
    """Return resolved settings for a fixture-backed test flow."""
    return Config.from_values(
        family=family,
        values={"target": "local", "dry_run": dry_run, "mirror_host": mirror_host},
        environment={},
    )


# endregion FUNC_make_config


# region FUNC_test_packages_fixture_selects_latest_rewrites_and_deduplicates
# PURPOSE: Verify the package policy transforms real MVP fixture records into a family-owned latest-only plan.
def test_packages_fixture_selects_latest_rewrites_and_deduplicates() -> None:
    """Selected package archives are unique and published index URLs use the mirror host."""
    plan, target = run_fixture_flow(
        make_config(IndexFamily.PACKAGES),
        present_keys=("packages/obsolete.tar.bz2", "libraries/protected.zip"),
    )

    assert plan.releases == (
        "arduino:avr@1.8.8",
        "arduino:mbed_nano@4.6.0",
        "arduino:mbed_rp2040@4.6.0",
        "arduino:megaavr@1.8.8",
        "arduino:sam@1.6.12",
        "arduino:samd@1.8.14",
    )
    assert len(plan.archive_keys) == len(set(plan.archive_keys))
    assert "packages/cores/staging/avr-1.8.8.tar.bz2" in plan.archive_keys
    avr_archive = next(
        archive
        for archive in plan.archives
        if archive.key == "packages/cores/staging/avr-1.8.8.tar.bz2"
    )
    assert avr_archive.source_url.endswith("/cores/staging/avr-1.8.8.tar.bz2")
    assert (
        avr_archive.sha256
        == "c816b6e9326cebe7721514288deeaf315affdef42049beb3f6cbbc4b7920304a"
    )
    assert avr_archive.size == _AVR_ARCHIVE_SIZE
    assert plan.stale_keys == ("packages/obsolete.tar.bz2",)
    package = plan.index["packages"][0]
    assert package["platforms"][0]["url"].startswith(
        "https://mirror.test.invalid/packages/"
    )
    assert target.operations == [
        "packages:list",
        "packages:archives",
        "packages:index",
        "packages:cleanup",
    ]


# endregion FUNC_test_packages_fixture_selects_latest_rewrites_and_deduplicates


# region FUNC_test_libraries_fixture_selects_latest_and_preserves_fields
# PURPOSE: Verify library latest selection uses exact names and keeps unrecognized selected-record fields.
def test_libraries_fixture_selects_latest_and_preserves_fields() -> None:
    """Libraries select stable latest releases and rewrite only their archive URLs."""
    plan, target = run_fixture_flow(
        make_config(IndexFamily.LIBRARIES),
        present_keys=("libraries/Servo-1.2.zip", "packages/protected.tar.bz2"),
    )

    assert plan.releases == ("Servo@1.2.2", "WiFiNINA@1.8.13")
    assert plan.archive_keys == (
        "libraries/libraries/Servo-1.2.2.zip",
        "libraries/libraries/WiFiNINA-1.8.13.zip",
    )
    assert plan.stale_keys == ("libraries/Servo-1.2.zip",)
    libraries = {library["name"]: library for library in plan.index["libraries"]}
    assert libraries["Servo"]["customField"] == {"preserved": True}
    assert (
        libraries["Servo"]["url"]
        == "https://mirror.test.invalid/libraries/libraries/Servo-1.2.2.zip"
    )
    assert target.operations == [
        "libraries:list",
        "libraries:archives",
        "libraries:index",
        "libraries:cleanup",
    ]


# endregion FUNC_test_libraries_fixture_selects_latest_and_preserves_fields


# region FUNC_test_external_releases_bypass_origin_latest_selection_and_rewrite
# PURPOSE: Verify external releases stay intact and cannot displace the latest origin-host release for either index family.
def test_external_releases_bypass_origin_latest_selection_and_rewrite() -> None:
    """Origin releases are filtered independently while external releases remain unchanged."""
    origin = "https://downloads.arduino.test"
    external_url = "https://github.example.invalid/releases/release-9.0.zip"
    packages = LatestPackagesPolicy(
        mirror_host="https://mirror.test.invalid",
        origin_host=origin,
        architectures=("avr",),
        package_names=("arduino",),
    ).select(
        {
            "packages": [
                {
                    "name": "arduino",
                    "platforms": [
                        {
                            "architecture": "avr",
                            "version": "1.0",
                            "url": f"{origin}/cores/avr-1.0.tar.bz2",
                        },
                        {
                            "architecture": "avr",
                            "version": "1.1",
                            "url": f"{origin}/cores/avr-1.1.tar.bz2",
                        },
                        {
                            "architecture": "avr",
                            "version": "9.0",
                            "url": external_url,
                        },
                    ],
                    "tools": [],
                }
            ]
        }
    )
    libraries = LatestLibrariesPolicy(
        mirror_host="https://mirror.test.invalid", origin_host=origin
    ).select(
        {
            "libraries": [
                {
                    "name": "Example",
                    "version": "1.0",
                    "url": f"{origin}/Example-1.0.zip",
                },
                {
                    "name": "Example",
                    "version": "1.1",
                    "url": f"{origin}/Example-1.1.zip",
                },
                {"name": "Example", "version": "9.0", "url": external_url},
            ]
        }
    )

    package_urls = [
        platform["url"] for platform in packages.index["packages"][0]["platforms"]
    ]
    library_urls = [library["url"] for library in libraries.index["libraries"]]
    assert package_urls == [
        "https://mirror.test.invalid/packages/cores/avr-1.1.tar.bz2",
        external_url,
    ]
    assert library_urls == [
        "https://mirror.test.invalid/libraries/Example-1.1.zip",
        external_url,
    ]
    assert packages.archive_keys == ("packages/cores/avr-1.1.tar.bz2",)
    assert libraries.archive_keys == ("libraries/Example-1.1.zip",)


# endregion FUNC_test_external_releases_bypass_origin_latest_selection_and_rewrite


# region FUNC_test_dry_run_builds_plan_without_target_interaction
# PURPOSE: Prove dry run reports transformed work without reading or mutating target state.
def test_dry_run_builds_plan_without_target_interaction() -> None:
    """Dry run creates a package plan and leaves its recording target unused."""
    config = make_config(IndexFamily.PACKAGES, dry_run=True)
    plan, target = run_fixture_flow(config, present_keys=("packages/stale.tar.bz2",))

    assert plan.archive_keys
    assert plan.stale_keys == ()
    assert target.operations == []


# endregion FUNC_test_dry_run_builds_plan_without_target_interaction


# region FUNC_test_empty_origin_selection_preserves_target_state
# PURPOSE: Verify a plan without origin archives cannot replace a visible index or remove family-owned archives.
def test_empty_origin_selection_preserves_target_state() -> None:
    """An external-only library index returns its plan without target interaction."""
    target = RecordingPublicationTarget(present_keys=("libraries/obsolete.zip",))
    use_case = PublishFamily(
        source=FixtureIndexSource(
            family=IndexFamily.LIBRARIES,
            raw_index={
                "libraries": [
                    {
                        "name": "External",
                        "version": "1.0.0",
                        "url": "https://github.example.invalid/External-1.0.0.zip",
                    }
                ]
            },
        ),
        selection=LatestLibrariesPolicy(
            mirror_host="https://mirror.test.invalid",
            origin_host="https://downloads.arduino.test",
        ),
        target=target,
    )

    plan = use_case.run(IndexFamily.LIBRARIES)

    assert plan.archives == ()
    assert plan.stale_keys == ()
    assert target.operations == []
    assert target.index_replaced is False


# endregion FUNC_test_empty_origin_selection_preserves_target_state


# region FUNC_test_configuration_cli_precedence_and_family_inputs
# PURPOSE: Verify explicit CLI values override non-empty environment and each family uses its own index variable.
def test_configuration_cli_precedence_and_family_inputs() -> None:
    """Configuration applies the committed CLI/environment/default contract."""
    environment = {
        "MIRROR_HOST": "https://environment.invalid",
        "PACKAGES_INPUT_INDEX": "packages-environment.json",
        "LIBRARIES_INPUT_INDEX": "libraries-environment.json",
        "ARCHITECTURES": "sam",
        "PACKAGES": "builtin",
        "DRY_RUN": "true",
    }
    packages = Config.from_values(
        family=IndexFamily.PACKAGES,
        values={"mirror_host": "https://cli.invalid", "dry_run": False},
        environment=environment,
    )
    libraries = Config.from_values(
        family=IndexFamily.LIBRARIES,
        values={},
        environment=environment,
    )

    assert packages.mirror_host == "https://cli.invalid"
    assert packages.input_index == "packages-environment.json"
    assert packages.architectures == ("sam",)
    assert packages.package_names == ("builtin",)
    assert packages.dry_run is False
    assert libraries.input_index == "libraries-environment.json"
    assert libraries.dry_run is True


# endregion FUNC_test_configuration_cli_precedence_and_family_inputs


# region FUNC_test_configuration_rejects_incomplete_s3_settings
# PURPOSE: Verify S3 settings are rejected before a publication-capable composition root can start publication.
def test_configuration_rejects_incomplete_s3_settings() -> None:
    """S3 validation requires its bucket and both credentials."""
    config = Config.from_values(family=IndexFamily.PACKAGES, values={}, environment={})

    with pytest.raises(ValueError, match="s3 target requires"):
        config.validate()


# endregion FUNC_test_configuration_rejects_incomplete_s3_settings


# region FUNC_test_archive_failure_preserves_index_and_does_not_start_other_family
# PURPOSE: Demonstrate a library failure stops before index replacement and leaves the independent package pipeline untouched.
def test_archive_failure_preserves_index_and_does_not_start_other_family() -> None:
    """A failed library archive boundary does not replace an index or run packages."""
    config = make_config(IndexFamily.LIBRARIES)
    target = RecordingPublicationTarget(fail_archives=True)
    use_case = PublishFamily(
        source=FixtureIndexSource(
            family=IndexFamily.LIBRARIES,
            raw_index={
                "libraries": [
                    {
                        "name": "Failure",
                        "version": "1.0.0",
                        "url": "http://fixture.arduino.test/Failure-1.0.0.zip",
                    }
                ]
            },
        ),
        selection=LatestLibrariesPolicy(
            mirror_host=config.mirror_host, origin_host="http://fixture.arduino.test"
        ),
        target=target,
    )

    with pytest.raises(TargetArchiveError):
        use_case.run(IndexFamily.LIBRARIES)

    assert target.operations == ["libraries:list", "libraries:archives"]
    assert target.index_replaced is False


# endregion FUNC_test_archive_failure_preserves_index_and_does_not_start_other_family


# region FUNC_test_cancellation_after_archive_boundary_skips_index_and_cleanup
# PURPOSE: Verify cooperative cancellation after archive work prevents the next index and stale-cleanup operations.
def test_cancellation_after_archive_boundary_skips_index_and_cleanup() -> None:
    """A cancellation at the next boundary keeps the prior index and stale keys intact."""
    target = RecordingPublicationTarget()
    use_case = PublishFamily(
        source=FixtureIndexSource(
            family=IndexFamily.LIBRARIES,
            raw_index={
                "libraries": [
                    {
                        "name": "Cancellation",
                        "version": "1.0.0",
                        "url": "http://fixture.arduino.test/Cancellation-1.0.0.zip",
                    }
                ]
            },
        ),
        selection=LatestLibrariesPolicy(
            mirror_host="https://mirror.test.invalid",
            origin_host="http://fixture.arduino.test",
        ),
        target=target,
    )

    def check_cancelled() -> None:
        if target.operations[-1:] == ["libraries:archives"]:
            raise PublicationCancelledError(signal.SIGTERM)

    with pytest.raises(PublicationCancelledError) as error:
        use_case.run(IndexFamily.LIBRARIES, check_cancelled=check_cancelled)

    assert error.value.exit_code == _SIGTERM_EXIT
    assert target.operations == ["libraries:list", "libraries:archives"]
    assert target.index_replaced is False


# endregion FUNC_test_cancellation_after_archive_boundary_skips_index_and_cleanup


# region FUNC_test_successful_flow_emits_trace_records
# PURPOSE: Verify the application exposes source, selection, reconciliation, and publication boundaries through structured debug records.
def test_successful_flow_emits_trace_records(caplog: pytest.LogCaptureFixture) -> None:
    """A successful flow records every boundary with its family identity."""
    caplog.set_level(logging.DEBUG, logger="arduino_mirror.application.publication")

    plan, _ = run_fixture_flow(make_config(IndexFamily.PACKAGES))
    trace_records = [
        record for record in caplog.records if record.levelno == logging.DEBUG
    ]

    assert [record.getMessage() for record in trace_records] == [
        "SOURCE_FETCHED",
        "PLAN_SELECTED",
        "STALE_PLANNED",
        "ARCHIVES_PUBLISHED",
        "INDEX_REPLACED",
        "STALE_CLEANED",
    ]
    assert [extra_fields(record) for record in trace_records] == [
        {"family": IndexFamily.PACKAGES},
        {
            "archive_count": len(plan.archives),
            "family": IndexFamily.PACKAGES,
            "release_count": len(plan.releases),
        },
        {
            "family": IndexFamily.PACKAGES,
            "stale_count": len(plan.stale_keys),
            "to_publish_count": len(plan.archives_to_publish),
        },
        {
            "archive_count": len(plan.archives_to_publish),
            "family": IndexFamily.PACKAGES,
        },
        {"family": IndexFamily.PACKAGES},
        {"family": IndexFamily.PACKAGES, "stale_count": len(plan.stale_keys)},
    ]


# endregion FUNC_test_successful_flow_emits_trace_records
