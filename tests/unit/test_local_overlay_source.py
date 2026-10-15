# region MODULE_CONTRACT
# PURPOSE: Prove optional local index overlays merge deterministically before family selection without reaching publication targets on invalid input.
# SCOPE:
# - Overlay configuration, JSON validation, schema-aware package/library merges, pinned selection, and trace records.
# - NOT: Real HTTP retrieval or target storage behavior.
# KEYWORDS: unit test, local overlay, index source, packages, libraries, pinning
# endregion MODULE_CONTRACT

"""Tests for local Arduino index overlay preparation."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

import pytest

from arduino_mirror.application import LatestPackagesPolicy, PublishFamily
from arduino_mirror.domain import IndexFamily, PinnedTool
from arduino_mirror.entrypoints.cli import _build_parser
from arduino_mirror.entrypoints.config import Config
from arduino_mirror.infra.local_overlay_source import LocalOverlayIndexSource
from tests.doubles import FixtureIndexSource, RecordingPublicationTarget
from tests.log_assertions import extra_fields

_ORIGIN = "https://downloads.arduino.test"
_MIRROR = "https://mirror.test.invalid"


def _write_overlay(tmp_path: Path, payload: object) -> Path:
    """Write one JSON overlay and return its path."""
    path = tmp_path / "overlay.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _source(
    family: IndexFamily, index: dict[str, object], path: Path
) -> LocalOverlayIndexSource:
    """Return one overlay source around a deterministic remote index."""
    return LocalOverlayIndexSource(
        source=FixtureIndexSource(family=family, raw_index=index), path=path
    )


# region FUNC_test_local_overlay_configuration_is_family_scoped
# PURPOSE: Verify CLI precedence and independent family environment variables select exactly one local overlay path.
def test_local_overlay_configuration_is_family_scoped() -> None:
    """A selected family resolves only its CLI value or its own environment variable."""
    packages = Config.from_values(
        family=IndexFamily.PACKAGES,
        values={"local_index": "cli-packages.json"},
        environment={
            "PACKAGES_LOCAL_INDEX": "environment-packages.json",
            "LIBRARIES_LOCAL_INDEX": "libraries.json",
        },
    )
    libraries = Config.from_values(
        family=IndexFamily.LIBRARIES,
        values={},
        environment={"PACKAGES_LOCAL_INDEX": "packages.json"},
    )
    library_environment = Config.from_values(
        family=IndexFamily.LIBRARIES,
        values={},
        environment={"LIBRARIES_LOCAL_INDEX": "libraries.json"},
    )

    assert packages.local_index == Path("cli-packages.json")
    assert libraries.local_index is None
    assert library_environment.local_index == Path("libraries.json")
    assert (
        _build_parser()
        .parse_args(["packages", "--local-index", "overlay.json"])
        .local_index
        == "overlay.json"
    )


# endregion FUNC_test_local_overlay_configuration_is_family_scoped


# region FUNC_test_packages_overlay_merges_tools_and_host_systems
# PURPOSE: Prove a minimal builtin overlay adds tool versions and host systems without erasing remote package content.
def test_packages_overlay_merges_tools_and_host_systems(tmp_path: Path) -> None:
    """Matching system hosts overlay remote fields while new hosts and versions append."""
    remote: dict[str, object] = {
        "packages": [
            {
                "name": "builtin",
                "maintainer": "Remote",
                "platforms": [{"architecture": "avr", "version": "1.0.0"}],
                "tools": [
                    {
                        "name": "serial-discovery",
                        "version": "1.0.0",
                        "systems": [
                            {"host": "x86_64-pc-linux-gnu", "url": "remote-linux"},
                            {"host": "x86_64-pc-windows-msvc", "url": "remote-windows"},
                        ],
                    }
                ],
            }
        ]
    }
    path = _write_overlay(
        tmp_path,
        {
            "packages": [
                {
                    "name": "builtin",
                    "maintainer": "Local",
                    "tools": [
                        {
                            "name": "serial-discovery",
                            "version": "1.0.0",
                            "systems": [
                                {"host": "x86_64-pc-linux-gnu", "url": "local-linux"},
                                {"host": "aarch64-apple-darwin", "url": "local-macos"},
                            ],
                        },
                        {
                            "name": "serial-discovery",
                            "version": "1.1.0",
                            "systems": [
                                {"host": "x86_64-pc-linux-gnu", "url": "new-linux"}
                            ],
                        },
                    ],
                }
            ]
        },
    )

    merged = _source(IndexFamily.PACKAGES, remote, path).fetch(IndexFamily.PACKAGES)

    packages = cast(list[dict[str, object]], merged["packages"])
    package = packages[0]
    assert package["maintainer"] == "Local"
    assert package["platforms"] == [{"architecture": "avr", "version": "1.0.0"}]
    tools = cast(list[dict[str, object]], package["tools"])
    assert [(tool["name"], tool["version"]) for tool in tools] == [
        ("serial-discovery", "1.0.0"),
        ("serial-discovery", "1.1.0"),
    ]
    assert tools[0]["systems"] == [
        {"host": "x86_64-pc-linux-gnu", "url": "local-linux"},
        {"host": "x86_64-pc-windows-msvc", "url": "remote-windows"},
        {"host": "aarch64-apple-darwin", "url": "local-macos"},
    ]


# endregion FUNC_test_packages_overlay_merges_tools_and_host_systems


# region FUNC_test_libraries_overlay_retains_remote_order_and_appends_local_records
# PURPOSE: Verify a matching library overlays supplied fields in place and a local-only release appends deterministically.
def test_libraries_overlay_retains_remote_order_and_appends_local_records(
    tmp_path: Path,
) -> None:
    """Library identity uses name and version while unspecified remote fields remain."""
    path = _write_overlay(
        tmp_path,
        {
            "libraries": [
                {"name": "Beta", "version": "1.0.0", "sentence": "Local"},
                {"name": "Gamma", "version": "1.0.0", "url": "local-gamma"},
            ]
        },
    )

    merged = _source(
        IndexFamily.LIBRARIES,
        {
            "libraries": [
                {"name": "Alpha", "version": "1.0.0", "url": "remote-alpha"},
                {"name": "Beta", "version": "1.0.0", "url": "remote-beta"},
            ]
        },
        path,
    ).fetch(IndexFamily.LIBRARIES)

    libraries = cast(list[dict[str, object]], merged["libraries"])
    assert [(library["name"], library["version"]) for library in libraries] == [
        ("Alpha", "1.0.0"),
        ("Beta", "1.0.0"),
        ("Gamma", "1.0.0"),
    ]
    assert libraries[1] == {
        "name": "Beta",
        "version": "1.0.0",
        "sentence": "Local",
        "url": "remote-beta",
    }


# endregion FUNC_test_libraries_overlay_retains_remote_order_and_appends_local_records


# region FUNC_test_overlay_validation_fails_before_publication_target
# PURPOSE: Ensure unusable local JSON stops publication while the target remains untouched.
@pytest.mark.parametrize(
    ("content", "expected_error"),
    [
        ("{", json.JSONDecodeError),
        ("[]", TypeError),
        ('{"packages": {}}', TypeError),
        ('{"packages": [{"name": "builtin", "tools": [{"name": "x"}]}]}', ValueError),
    ],
)
def test_overlay_validation_fails_before_publication_target(
    tmp_path: Path, content: str, expected_error: type[Exception]
) -> None:
    """Invalid root, collection, or local identity cannot begin target work."""
    path = tmp_path / "invalid.json"
    path.write_text(content, encoding="utf-8")
    target = RecordingPublicationTarget()
    use_case = PublishFamily(
        source=_source(IndexFamily.PACKAGES, {"packages": []}, path),
        selection=LatestPackagesPolicy(
            mirror_host=_MIRROR,
            origin_host=_ORIGIN,
            architectures=(),
            package_names=(),
        ),
        target=target,
    )

    with pytest.raises(expected_error):
        use_case.run(IndexFamily.PACKAGES)

    assert target.operations == []


# endregion FUNC_test_overlay_validation_fails_before_publication_target


# region FUNC_test_overlay_tool_version_is_available_to_pinning
# PURPOSE: Prove exact package pins are evaluated after a local tool version joins the remote index.
def test_overlay_tool_version_is_available_to_pinning(tmp_path: Path) -> None:
    """An exact pin retains the tool version supplied only by the overlay."""
    path = _write_overlay(
        tmp_path,
        {
            "packages": [
                {
                    "name": "builtin",
                    "tools": [
                        {
                            "name": "serial-discovery",
                            "version": "1.1.0",
                            "systems": [
                                {
                                    "host": "x86_64-pc-linux-gnu",
                                    "url": f"{_ORIGIN}/tools/serial-discovery-1.1.0.tar.gz",
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )
    raw_index = _source(IndexFamily.PACKAGES, {"packages": []}, path).fetch(
        IndexFamily.PACKAGES
    )

    plan = LatestPackagesPolicy(
        mirror_host=_MIRROR,
        origin_host=_ORIGIN,
        architectures=(),
        package_names=(),
        pinned_tools=(PinnedTool("builtin", "serial-discovery", "1.1.0"),),
    ).select(raw_index)

    assert plan.archive_keys == ("p/tools/serial-discovery-1.1.0.tar.gz",)
    assert plan.index["packages"][0]["tools"][0]["version"] == "1.1.0"


# endregion FUNC_test_overlay_tool_version_is_available_to_pinning


# region FUNC_test_overlay_source_emits_loading_and_merge_traces
# PURPOSE: Verify overlay source boundaries identify the family, path, and collection counts without exposing index content.
def test_overlay_source_emits_loading_and_merge_traces(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Successful overlay preparation records loading and deterministic count traces."""
    caplog.set_level(logging.DEBUG, logger="arduino_mirror.infra.local_overlay_source")
    path = _write_overlay(
        tmp_path, {"libraries": [{"name": "Local", "version": "1.0.0"}]}
    )

    _source(
        IndexFamily.LIBRARIES,
        {"libraries": [{"name": "Remote", "version": "1.0.0"}]},
        path,
    ).fetch(IndexFamily.LIBRARIES)

    records = [record for record in caplog.records if record.levelno == logging.DEBUG]
    assert [record.getMessage() for record in records] == [
        "LOCAL_OVERLAY_LOADING",
        "LOCAL_OVERLAY_MERGED",
    ]
    assert [extra_fields(record) for record in records] == [
        {"family": IndexFamily.LIBRARIES, "path": str(path)},
        {
            "family": IndexFamily.LIBRARIES,
            "merged_record_count": 2,
            "overlay_record_count": 1,
            "remote_record_count": 1,
        },
    ]


# endregion FUNC_test_overlay_source_emits_loading_and_merge_traces
