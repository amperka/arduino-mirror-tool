# region MODULE_CONTRACT
# PURPOSE: Prove the operator-facing GCS CLI composes real HTTP/SDK adapters and previews both families without credentials or storage access.
# SCOPE:
# - In-process CLI against local HTTP origin and loopback Storage JSON API; package overlays/pins and credential-free dry-run commands.
# - NOT: Installed console-script subprocess, WIF/ADC refresh, IAM, or live GCS; the shared stub covers small multipart uploads only.
# KEYWORDS: e2e, GCS, CLI, offline, dry run
# endregion MODULE_CONTRACT

"""Offline GCS CLI contracts using the real SDK for publication."""

from __future__ import annotations

import json
from pathlib import Path

import google.auth
import pytest
import requests
from google.cloud import storage

from arduino_mirror.entrypoints.cli import main
from tests.e2e.test_publication_cli import _http_root
from tests.integration.test_gcs_publication import (
    _BUCKET,
    _library_source,
    _storage_api,
)

__all__: list[str] = []


# region FUNC_test_gcs_cli_publishes_through_real_sdk_without_s3_credentials
# PURPOSE: Exercise real CLI argument resolution and composition through source HTTP, verification, SDK upload, and stale deletion.
def test_gcs_cli_publishes_through_real_sdk_without_s3_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archive_bytes = b"CLI GCS release"
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "DRY_RUN"):
        monkeypatch.delenv(name, raising=False)

    with _storage_api(monkeypatch) as service, _http_root(source) as origin:
        service.objects["managed/l/obsolete.zip"] = b"old"
        service.objects["managed/p/protected.tar.bz2"] = b"package family"
        index_url = _library_source(source, origin, archive_bytes)
        result = main(
            [
                "libraries",
                "--input",
                index_url,
                "--mirror-host",
                "https://mirror.test.invalid",
                "--target",
                "gcs",
                "--bucket",
                _BUCKET,
                "--prefix",
                "managed",
                "--retry-attempts",
                "2",
                "--retry-base-delay",
                "0",
            ],
        )

    assert result == 0
    assert capsys.readouterr().out == (
        "libraries: published 1 release(s), 1 archive(s), 1 stale\n"
    )
    assert service.events == [
        ("list", "managed/l/"),
        ("upload", "managed/l/Servo-1.0.0.zip"),
        ("upload", "managed/l/libraries/library_index.json"),
        ("delete", "managed/l/obsolete.zip"),
    ]
    assert service.objects["managed/l/Servo-1.0.0.zip"] == archive_bytes
    assert service.objects["managed/p/protected.tar.bz2"] == b"package family"
    index = json.loads(service.objects["managed/l/libraries/library_index.json"])
    assert index["libraries"][0]["url"] == (
        "https://mirror.test.invalid/l/Servo-1.0.0.zip"
    )


# endregion FUNC_test_gcs_cli_publishes_through_real_sdk_without_s3_credentials


# region FUNC_test_gcs_cli_publishes_package_overlay_and_both_pins
# PURPOSE: Preserve master's overlay and independent tool/platform pins through GCS composition and real SDK writes.
def test_gcs_cli_publishes_package_overlay_and_both_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archives = {"board.tar.gz": b"pinned platform", "tool.tar.gz": b"pinned tool"}
    for name, content in archives.items():
        (source / name).write_bytes(content)
    (source / "index.json").write_text('{"packages": []}', encoding="utf-8")
    overlay = tmp_path / "packages-overlay.json"
    environment = {
        "TARGET_KIND": "gcs",
        "TARGET_BUCKET": _BUCKET,
        "TARGET_PREFIX": "managed",
        "MIRROR_HOST": "https://mirror.test.invalid",
        "ARCHITECTURES": "excluded",
        "PACKAGES": "excluded",
        "PINNED_TOOLS": "overlay:compiler@1.0.0",
        "PINNED_PLATFORMS": "overlay:avr@1.0.0",
        "PACKAGES_LOCAL_INDEX": str(overlay),
        "LIBRARIES_LOCAL_INDEX": str(tmp_path / "must-not-read.json"),
        "RETRY_ATTEMPTS": "2",
        "RETRY_BASE_DELAY": "0",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "DRY_RUN"):
        monkeypatch.delenv(name, raising=False)

    with _storage_api(monkeypatch) as service, _http_root(source) as origin:
        overlay.write_text(
            json.dumps(
                {
                    "packages": [
                        {
                            "name": "overlay",
                            "platforms": [
                                {
                                    "architecture": "avr",
                                    "version": "1.0.0",
                                    "url": f"{origin}/board.tar.gz",
                                    "size": len(archives["board.tar.gz"]),
                                },
                            ],
                            "tools": [
                                {
                                    "name": "compiler",
                                    "version": "1.0.0",
                                    "systems": [
                                        {
                                            "host": "x86_64-pc-linux-gnu",
                                            "url": f"{origin}/tool.tar.gz",
                                            "size": len(archives["tool.tar.gz"]),
                                        },
                                        {
                                            "host": "aarch64-apple-darwin",
                                            "url": "https://vendor.invalid/tool.zip",
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ),
            encoding="utf-8",
        )
        protected = {
            "managed/l/protected.zip": b"library family",
            "xod-fix/protected.tar.gz": b"separate XOD publication",
        }
        service.objects.update(protected)
        service.objects["managed/p/obsolete.zip"] = b"old package"
        result = main(["packages", "--input", f"{origin}/index.json"])

    assert result == 0
    assert capsys.readouterr().out == (
        "packages: published 1 release(s), 2 archive(s), 1 stale\n"
    )
    assert service.events == [
        ("list", "managed/p/"),
        ("upload", "managed/p/board.tar.gz"),
        ("upload", "managed/p/tool.tar.gz"),
        ("upload", "managed/p/index.json"),
        ("delete", "managed/p/obsolete.zip"),
    ]
    for name, content in archives.items():
        assert service.objects[f"managed/p/{name}"] == content
    assert {key: service.objects[key] for key in protected} == protected
    index = json.loads(service.objects["managed/p/index.json"])
    assert len(index["packages"]) == 1
    package = index["packages"][0]
    assert package["name"] == "overlay"
    (platform,) = package["platforms"]
    assert (platform["architecture"], platform["version"]) == ("avr", "1.0.0")
    assert platform["url"] == "https://mirror.test.invalid/p/board.tar.gz"
    (tool,) = package["tools"]
    assert (tool["name"], tool["version"]) == ("compiler", "1.0.0")
    assert [system["url"] for system in tool["systems"]] == [
        "https://mirror.test.invalid/p/tool.tar.gz",
        "https://vendor.invalid/tool.zip",
    ]


# endregion FUNC_test_gcs_cli_publishes_package_overlay_and_both_pins


# region FUNC_test_gcs_cli_dry_run_never_constructs_client_or_discovers_adc
# PURPOSE: Verify both family previews fetch only their index and do not construct a GCS client, discover credentials, or retrieve archives.
@pytest.mark.parametrize("family", ["packages", "libraries"])
def test_gcs_cli_dry_run_never_constructs_client_or_discovers_adc(
    family: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.delenv("STORAGE_EMULATOR_HOST", raising=False)
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)

    def forbidden_cloud_access(*_args, **_kwargs):
        pytest.fail("dry run attempted client construction or ADC discovery")

    monkeypatch.setattr(storage, "Client", forbidden_cloud_access)
    monkeypatch.setattr(google.auth, "default", forbidden_cloud_access)
    original_request = requests.Session.request
    requested = []

    with _http_root(source) as origin:
        index_url = f"{origin}/index.json"
        record = {"version": "1.0.0", "url": f"{origin}/must-not-download.zip"}
        index = (
            {
                "packages": [
                    {
                        "name": "arduino",
                        "platforms": [{**record, "architecture": "avr"}],
                        "tools": [],
                    },
                ],
            }
            if family == "packages"
            else {"libraries": [{**record, "name": "Preview"}]}
        )
        (source / "index.json").write_text(json.dumps(index), encoding="utf-8")

        def index_only_request(self, method, url, *args, **kwargs):
            assert url == index_url, "dry run attempted non-index HTTP access"
            requested.append((method, url))
            return original_request(self, method, url, *args, **kwargs)

        monkeypatch.setattr(requests.Session, "request", index_only_request)
        result = main(
            [
                family,
                "--input",
                index_url,
                "--target",
                "gcs",
                "--bucket",
                _BUCKET,
                "--architectures",
                "avr",
                "--packages",
                "arduino",
                "--dry-run",
            ],
        )

    assert result == 0
    assert requested == [("get", index_url)]
    assert capsys.readouterr().out == (
        f"{family}: planned 1 release(s), 1 archive(s), 0 stale\n"
    )


# endregion FUNC_test_gcs_cli_dry_run_never_constructs_client_or_discovers_adc
