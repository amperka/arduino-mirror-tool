# region MODULE_CONTRACT
# PURPOSE: Prove the operator-facing GCS CLI composes real HTTP/SDK adapters and previews both families without credentials or storage access.
# SCOPE:
# - In-process CLI against local HTTP origin and loopback Storage JSON API; credential-free dry-run commands.
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
