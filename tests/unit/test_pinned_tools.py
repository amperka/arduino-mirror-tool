# region MODULE_CONTRACT
# PURPOSE: Prove exact pinned package tools are configured, selected, and diagnosed without broadening platform or library selection.
# SCOPE:
# - Pinned-tool configuration, package-index transformation, unavailable pins, and run-level warnings.
# - NOT: HTTP, storage adapter, or library selection implementation.
# KEYWORDS: unit test, package tools, pins, selection, warning
# endregion MODULE_CONTRACT

"""Focused tests for configured exact Boards Manager tools."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from arduino_mirror.application import (
    LatestLibrariesPolicy,
    LatestPackagesPolicy,
    PublishFamily,
)
from arduino_mirror.domain import (
    IndexFamily,
    PinnedPlatform,
    PinnedTool,
    PublicationPlan,
)
from arduino_mirror.entrypoints.cli import _build_parser
from arduino_mirror.entrypoints.config import (
    DEFAULT_PINNED_TOOLS,
    Config,
    TargetKind,
)
from arduino_mirror.entrypoints.di import make_publication_use_case
from tests.doubles import FixtureIndexSource, RecordingPublicationTarget
from tests.log_assertions import extra_fields

_ORIGIN = "https://downloads.arduino.test"
_MIRROR = "https://mirror.test.invalid"


def _tool(name: str, version: str, path: str) -> dict[str, object]:
    """Return one source-origin tool with one mirrorable system archive."""
    return {
        "name": name,
        "version": version,
        "systems": [{"url": f"{_ORIGIN}/{path}"}],
    }


def _platform(
    architecture: str,
    version: str,
    path: str,
    *,
    dependencies: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    """Return one source-origin platform with optional exact tool dependencies."""
    return {
        "architecture": architecture,
        "version": version,
        "url": f"{_ORIGIN}/{path}",
        "toolsDependencies": dependencies or [],
    }


def _package(
    name: str,
    *,
    tools: list[dict[str, object]],
    platforms: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Return a minimal package-index owner record."""
    return {
        "name": name,
        "maintainer": "Arduino",
        "platforms": platforms or [],
        "tools": tools,
    }


# region FUNC_test_pinned_tool_configuration_precedence_and_deduplication
# PURPOSE: Prove operator configuration produces one stable exact identity per requested tool.
def test_pinned_tool_configuration_precedence_and_deduplication() -> None:
    """CLI pins win over environment pins, trim whitespace, and deduplicate identities."""
    config = Config.from_values(
        family=IndexFamily.PACKAGES,
        values={
            "pinned_tools": " builtin:ctags@5.8-arduino11, builtin:ctags@5.8-arduino11 ",
            "pinned_platforms": " arduino:avr@1.8.8, arduino:avr@1.8.8 ",
        },
        environment={
            "PINNED_TOOLS": "builtin:serial-discovery@1.0.0",
            "PINNED_PLATFORMS": "arduino:samd@1.8.14",
        },
    )

    assert (
        PinnedTool("builtin", "ctags", "5.8-arduino11"),
        PinnedTool("builtin", "serial-discovery", "1.0.0"),
    ) == DEFAULT_PINNED_TOOLS
    assert (
        Config.from_values(
            family=IndexFamily.PACKAGES,
            values={},
            environment={"PINNED_TOOLS": ""},
        ).pinned_tools
        == DEFAULT_PINNED_TOOLS
    )
    assert config.pinned_tools == (PinnedTool("builtin", "ctags", "5.8-arduino11"),)
    assert config.pinned_platforms == (PinnedPlatform("arduino", "avr", "1.8.8"),)
    assert (
        Config.from_values(
            family=IndexFamily.PACKAGES, values={}, environment={}
        ).pinned_platforms
        == ()
    )
    parsed = _build_parser().parse_args(
        [
            "packages",
            "--pinned-tools",
            "builtin:ctags@5.8-arduino11",
            "--pinned-platforms",
            "arduino:avr@1.8.8",
        ]
    )
    assert parsed.pinned_tools == "builtin:ctags@5.8-arduino11"
    assert parsed.pinned_platforms == "arduino:avr@1.8.8"


# endregion FUNC_test_pinned_tool_configuration_precedence_and_deduplication


# region FUNC_test_pinned_tool_configuration_rejects_malformed_values
# PURPOSE: Ensure a typo cannot silently alter the tool retention policy.
@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        ("pinned_tools", "builtin:ctags", "pinned tools"),
        ("pinned_tools", ":ctags@5.8", "pinned tools"),
        ("pinned_tools", "builtin:@5.8", "pinned tools"),
        ("pinned_tools", "builtin:ctags@5.8,,x:y@1", "pinned tools"),
        ("pinned_platforms", "arduino:avr", "pinned platforms"),
        ("pinned_platforms", "arduino:@1.8.8", "pinned platforms"),
        (
            "pinned_platforms",
            "arduino:avr@1.8.8,,arduino:samd@1.8.14",
            "pinned platforms",
        ),
    ],
)
def test_pinned_selection_configuration_rejects_malformed_values(
    setting: str, value: str, message: str
) -> None:
    """Malformed non-empty pin lists fail during configuration resolution."""
    with pytest.raises(ValueError, match=message):
        Config.from_values(
            family=IndexFamily.PACKAGES,
            values={setting: value},
            environment={},
        )


# endregion FUNC_test_pinned_tool_configuration_rejects_malformed_values


# region FUNC_test_library_pins_are_ignored_and_public_constructors_remain_compatible
# PURPOSE: Preserve library isolation and positional construction contracts while adding package-only pin metadata.
def test_library_pins_are_ignored_and_public_constructors_remain_compatible() -> None:
    """Libraries bypass malformed pin input and older positional constructors retain their field meanings."""
    libraries = Config.from_values(
        family=IndexFamily.LIBRARIES,
        values={
            "pinned_tools": "not-a-pinned-tool",
            "pinned_platforms": "also-not-a-pinned-platform",
        },
        environment={
            "PINNED_TOOLS": "also-not-a-pinned-tool",
            "PINNED_PLATFORMS": "still-not-a-pinned-platform",
        },
    )
    direct = Config(
        IndexFamily.LIBRARIES,
        "https://downloads.arduino.test/libraries/library_index.json",
        _MIRROR,
        TargetKind.LOCAL,
        "",
        "",
        "",
        "",
        Path("mirror-out"),
        False,
        "",
        "",
        (),
        (),
        10,
        1.0,
    )
    positional_plan = PublicationPlan(
        IndexFamily.PACKAGES,
        (),
        (),
        {"packages": []},
        ("p/stale.zip",),
    )

    assert libraries.pinned_tools == ()
    assert libraries.pinned_platforms == ()
    assert direct.pinned_tools == ()
    assert direct.pinned_platforms == ()
    assert positional_plan.stale_keys == ("p/stale.zip",)
    assert positional_plan.skipped_pinned_tools == ()


# endregion FUNC_test_library_pins_are_ignored_and_public_constructors_remain_compatible


# region FUNC_test_pinned_tool_selection_adds_minimal_unconfigured_owner
# PURPOSE: Prove a pin remains independent from the package-name and platform filters.
def test_pinned_tool_selection_adds_minimal_unconfigured_owner() -> None:
    """A standalone pin retains only its owner metadata, tool, and rewritten archive."""
    policy = LatestPackagesPolicy(
        mirror_host=_MIRROR,
        origin_host=_ORIGIN,
        architectures=(),
        package_names=(),
        pinned_tools=(PinnedTool("builtin", "ctags", "5.8-arduino11"),),
    )

    plan = policy.select(
        {
            "packages": [
                _package(
                    "builtin",
                    tools=[
                        _tool(
                            "ctags",
                            "5.8-arduino11",
                            "tools/ctags-5.8-arduino11.tar.bz2",
                        ),
                        _tool("other", "1.0.0", "tools/other-1.0.0.tar.bz2"),
                    ],
                )
            ]
        }
    )

    assert plan.releases == ()
    assert plan.archive_keys == ("p/tools/ctags-5.8-arduino11.tar.bz2",)
    assert plan.index["packages"] == [
        {
            "name": "builtin",
            "maintainer": "Arduino",
            "platforms": [],
            "tools": [
                {
                    "name": "ctags",
                    "version": "5.8-arduino11",
                    "systems": [
                        {
                            "url": "https://mirror.test.invalid/p/tools/ctags-5.8-arduino11.tar.bz2"
                        }
                    ],
                }
            ],
        }
    ]


# endregion FUNC_test_pinned_tool_selection_adds_minimal_unconfigured_owner


# region FUNC_test_pinned_platform_selection_ignores_package_and_architecture_filters
# PURPOSE: Prove an exact platform pin is independent of configured latest-platform filters and retains its dependencies.
def test_pinned_platform_selection_ignores_package_and_architecture_filters() -> None:
    """A standalone platform pin retains only the exact requested platform and tools."""
    policy = LatestPackagesPolicy(
        mirror_host=_MIRROR,
        origin_host=_ORIGIN,
        architectures=(),
        package_names=(),
        pinned_platforms=(PinnedPlatform("arduino", "avr", "1.8.8"),),
    )

    plan = policy.select(
        {
            "packages": [
                _package(
                    "arduino",
                    platforms=[
                        _platform("avr", "1.8.7", "cores/avr-1.8.7.tar.bz2"),
                        _platform(
                            "avr",
                            "1.8.8",
                            "cores/avr-1.8.8.tar.bz2",
                            dependencies=[
                                {
                                    "packager": "arduino",
                                    "name": "avr-gcc",
                                    "version": "7.3.0",
                                }
                            ],
                        ),
                    ],
                    tools=[_tool("avr-gcc", "7.3.0", "tools/avr-gcc-7.3.0.tar.bz2")],
                )
            ]
        }
    )

    assert plan.releases == ("arduino:avr@1.8.8",)
    assert plan.archive_keys == (
        "p/cores/avr-1.8.8.tar.bz2",
        "p/tools/avr-gcc-7.3.0.tar.bz2",
    )
    package = plan.index["packages"][0]
    assert [platform["version"] for platform in package["platforms"]] == ["1.8.8"]
    assert [tool["name"] for tool in package["tools"]] == ["avr-gcc"]


# endregion FUNC_test_pinned_platform_selection_ignores_package_and_architecture_filters


# region FUNC_test_pinned_platform_merges_with_latest_selection_without_duplication
# PURPOSE: Ensure a platform selected by both policies remains one release and archive.
def test_pinned_platform_merges_with_latest_selection_without_duplication() -> None:
    """A pin overlapping the newest configured platform does not duplicate it."""
    policy = LatestPackagesPolicy(
        mirror_host=_MIRROR,
        origin_host=_ORIGIN,
        architectures=("avr",),
        package_names=("arduino",),
        pinned_platforms=(PinnedPlatform("arduino", "avr", "1.8.8"),),
    )

    plan = policy.select(
        {
            "packages": [
                _package(
                    "arduino",
                    platforms=[_platform("avr", "1.8.8", "cores/avr-1.8.8.tar.bz2")],
                    tools=[],
                )
            ]
        }
    )

    assert plan.releases == ("arduino:avr@1.8.8",)
    assert plan.archive_keys == ("p/cores/avr-1.8.8.tar.bz2",)
    assert len(plan.index["packages"][0]["platforms"]) == 1


# endregion FUNC_test_pinned_platform_merges_with_latest_selection_without_duplication


# region FUNC_test_pinned_tool_merges_with_platform_dependency_without_duplication
# PURPOSE: Ensure one exact tool selected for two reasons remains one index entry and one archive.
def test_pinned_tool_merges_with_platform_dependency_without_duplication() -> None:
    """A pin overlapping a platform dependency does not duplicate its tool or archive."""
    pin = PinnedTool("arduino", "tool", "1.0.0")
    policy = LatestPackagesPolicy(
        mirror_host=_MIRROR,
        origin_host=_ORIGIN,
        architectures=("avr",),
        package_names=("arduino",),
        pinned_tools=(pin, pin),
    )

    plan = policy.select(
        {
            "packages": [
                _package(
                    "arduino",
                    tools=[_tool("tool", "1.0.0", "tools/tool-1.0.0.zip")],
                    platforms=[
                        {
                            "architecture": "avr",
                            "version": "1.0.0",
                            "url": f"{_ORIGIN}/cores/avr-1.0.0.tar.bz2",
                            "toolsDependencies": [
                                {
                                    "packager": "arduino",
                                    "name": "tool",
                                    "version": "1.0.0",
                                }
                            ],
                        }
                    ],
                )
            ]
        }
    )

    package = plan.index["packages"][0]
    assert plan.releases == ("arduino:avr@1.0.0",)
    assert plan.archive_keys == ("p/cores/avr-1.0.0.tar.bz2", "p/tools/tool-1.0.0.zip")
    assert [tool["name"] for tool in package["tools"]] == ["tool"]


# endregion FUNC_test_pinned_tool_merges_with_platform_dependency_without_duplication


# region FUNC_test_missing_or_unavailable_pinned_tool_is_diagnosed
# PURPOSE: Prove pins never silently select a replacement version after an absence or archive failure.
def test_missing_or_unavailable_pinned_tool_is_diagnosed() -> None:
    """Selection returns exact skip diagnostics for missing and unavailable pins."""
    missing = PinnedTool("builtin", "missing", "1.0.0")
    unavailable = PinnedTool("builtin", "ctags", "5.8-arduino11")
    policy = LatestPackagesPolicy(
        mirror_host=_MIRROR,
        origin_host=_ORIGIN,
        architectures=(),
        package_names=(),
        pinned_tools=(missing, unavailable),
    )

    plan = policy.select(
        {
            "packages": [
                _package(
                    "builtin",
                    tools=[_tool("ctags", "5.8-arduino11", "tools/ctags.tar.bz2")],
                )
            ]
        },
        unavailable_archive_keys=frozenset({"p/tools/ctags.tar.bz2"}),
    )

    assert plan.archives == ()
    assert [(skip.tool, skip.reason) for skip in plan.skipped_pinned_tools] == [
        (unavailable, "origin system archive unavailable"),
        (missing, "not found in source index"),
    ]


# endregion FUNC_test_missing_or_unavailable_pinned_tool_is_diagnosed


# region FUNC_test_missing_or_unavailable_pinned_platform_is_diagnosed
# PURPOSE: Prove platform pins never silently select a replacement after an absence or archive failure.
def test_missing_or_unavailable_pinned_platform_is_diagnosed() -> None:
    """Selection returns exact skip diagnostics for unavailable and missing platform pins."""
    missing = PinnedPlatform("arduino", "samd", "1.8.14")
    unavailable = PinnedPlatform("arduino", "avr", "1.8.8")
    policy = LatestPackagesPolicy(
        mirror_host=_MIRROR,
        origin_host=_ORIGIN,
        architectures=(),
        package_names=(),
        pinned_platforms=(missing, unavailable),
    )

    plan = policy.select(
        {
            "packages": [
                _package(
                    "arduino",
                    platforms=[_platform("avr", "1.8.8", "cores/avr-1.8.8.tar.bz2")],
                    tools=[],
                )
            ]
        },
        unavailable_archive_keys=frozenset({"p/cores/avr-1.8.8.tar.bz2"}),
    )

    assert plan.archives == ()
    assert [(skip.platform, skip.reason) for skip in plan.skipped_pinned_platforms] == [
        (unavailable, "origin platform archive unavailable"),
        (missing, "not found in source index"),
    ]


# endregion FUNC_test_missing_or_unavailable_pinned_platform_is_diagnosed


# region FUNC_test_pinned_platform_is_retained_when_any_matching_record_is_available
# PURPOSE: Ensure duplicate source records only skip a pin when every exact candidate is unavailable.
def test_pinned_platform_is_retained_when_any_matching_record_is_available() -> None:
    """An unavailable duplicate does not hide another available exact platform record."""
    pin = PinnedPlatform("arduino", "avr", "1.8.8")
    policy = LatestPackagesPolicy(
        mirror_host=_MIRROR,
        origin_host=_ORIGIN,
        architectures=(),
        package_names=(),
        pinned_platforms=(pin,),
    )

    plan = policy.select(
        {
            "packages": [
                _package(
                    "arduino",
                    platforms=[
                        _platform("avr", "1.8.8", "cores/available-avr-1.8.8.tar.bz2"),
                        _platform(
                            "avr", "1.8.8", "cores/unavailable-avr-1.8.8.tar.bz2"
                        ),
                    ],
                    tools=[],
                )
            ]
        },
        unavailable_archive_keys=frozenset({"p/cores/unavailable-avr-1.8.8.tar.bz2"}),
    )

    assert plan.releases == ("arduino:avr@1.8.8",)
    assert plan.skipped_pinned_platforms == ()


# endregion FUNC_test_pinned_platform_is_retained_when_any_matching_record_is_available


# region FUNC_test_pinned_tool_warning_is_deduplicated_across_replans
# PURPOSE: Ensure re-planning retries do not flood operator output with the same skipped-pin warning.
def test_pinned_tool_warning_is_deduplicated_across_replans(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each pin is warned once even when successive archive failures re-plan selection."""
    pins = (
        PinnedTool("builtin", "first", "1.0.0"),
        PinnedTool("builtin", "second", "1.0.0"),
    )
    source = FixtureIndexSource(
        family=IndexFamily.PACKAGES,
        raw_index={
            "packages": [
                _package(
                    "builtin",
                    tools=[
                        _tool("first", "1.0.0", "tools/first.zip"),
                        _tool("second", "1.0.0", "tools/second.zip"),
                    ],
                )
            ]
        },
    )
    use_case = PublishFamily(
        source=source,
        selection=LatestPackagesPolicy(
            mirror_host=_MIRROR,
            origin_host=_ORIGIN,
            architectures=(),
            package_names=(),
            pinned_tools=pins,
        ),
        target=RecordingPublicationTarget(
            unavailable_archive_keys={"p/tools/first.zip", "p/tools/second.zip"}
        ),
    )
    caplog.set_level(logging.WARNING, logger="arduino_mirror.application.publication")

    plan = use_case.run(IndexFamily.PACKAGES)

    assert plan.archives == ()
    warnings = [
        record
        for record in caplog.records
        if record.getMessage().startswith("Pinned tool skipped:")
    ]
    assert [
        (extra_fields(record)["pinned_tool"], extra_fields(record)["reason"])
        for record in warnings
    ] == [
        ("builtin:first@1.0.0", "origin system archive unavailable"),
        ("builtin:second@1.0.0", "origin system archive unavailable"),
    ]


# endregion FUNC_test_pinned_tool_warning_is_deduplicated_across_replans


# region FUNC_test_pinned_platform_warning_is_deduplicated_across_replans
# PURPOSE: Ensure re-planning warns once for a skipped platform pin.
def test_pinned_platform_warning_is_deduplicated_across_replans(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A permanently unavailable exact platform emits one warning after fallback selection."""
    pin = PinnedPlatform("arduino", "avr", "1.8.8")
    source = FixtureIndexSource(
        family=IndexFamily.PACKAGES,
        raw_index={
            "packages": [
                _package(
                    "arduino",
                    platforms=[_platform("avr", "1.8.8", "cores/avr-1.8.8.tar.bz2")],
                    tools=[],
                )
            ]
        },
    )
    use_case = PublishFamily(
        source=source,
        selection=LatestPackagesPolicy(
            mirror_host=_MIRROR,
            origin_host=_ORIGIN,
            architectures=(),
            package_names=(),
            pinned_platforms=(pin,),
        ),
        target=RecordingPublicationTarget(
            unavailable_archive_keys={"p/cores/avr-1.8.8.tar.bz2"}
        ),
    )
    caplog.set_level(logging.WARNING, logger="arduino_mirror.application.publication")

    plan = use_case.run(IndexFamily.PACKAGES)

    assert plan.archives == ()
    warnings = [
        record
        for record in caplog.records
        if record.getMessage().startswith("Pinned platform skipped:")
    ]
    assert [
        (extra_fields(record)["pinned_platform"], extra_fields(record)["reason"])
        for record in warnings
    ] == [("arduino:avr@1.8.8", "origin platform archive unavailable")]


# endregion FUNC_test_pinned_platform_warning_is_deduplicated_across_replans


# region FUNC_test_libraries_policy_has_no_pinned_tool_setting
# PURPOSE: Guard independent library selection from package-only pin configuration.
def test_libraries_policy_has_no_pinned_tool_setting() -> None:
    """Library selection remains independent of package exact-tool identities."""
    config = Config.from_values(
        family=IndexFamily.LIBRARIES,
        values={
            "target": "local",
            "pinned_tools": "builtin:ctags@5.8-arduino11",
            "pinned_platforms": "arduino:avr@1.8.8",
        },
        environment={},
    )
    policy = make_publication_use_case(config).selection
    assert isinstance(policy, LatestLibrariesPolicy)

    plan = policy.select(
        {
            "libraries": [
                {"name": "Example", "version": "1.0.0", "url": f"{_ORIGIN}/Example.zip"}
            ]
        }
    )

    assert not hasattr(policy, "pinned_tools")
    assert not hasattr(policy, "pinned_platforms")
    assert plan.releases == ("Example@1.0.0",)


# endregion FUNC_test_libraries_policy_has_no_pinned_tool_setting
