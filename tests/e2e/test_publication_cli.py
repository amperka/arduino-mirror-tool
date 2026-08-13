# region MODULE_CONTRACT
# PURPOSE: Prove an operator can run the CLI against an HTTP source and obtain a published Library Manager index and archive in a local target.
# SCOPE: One complete publication libraries command with a temporary HTTP origin and local target.
# KEYWORDS: e2e test, publication CLI, libraries, local target
# endregion MODULE_CONTRACT

"""End-to-end test for the publication CLI."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from arduino_mirror.domain import IndexFamily
from arduino_mirror.entrypoints.cli import main
from tests.log_assertions import extra_fields

_SIGTERM_EXIT = 128 + int(signal.SIGTERM)


@contextmanager
def _http_root(directory: Path) -> Iterator[str]:
    """Serve one temporary source directory and yield its HTTP origin URL."""
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=directory)
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{server.server_address[0]}:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


# region FUNC_test_libraries_cli_publishes_verified_archive_and_index
# PURPOSE: Verify the operator-visible command completes publication publication with the generated index pointing at the mirror host.
def test_libraries_cli_publishes_verified_archive_and_index(
    tmp_path: Path, capsys, log_records
) -> None:
    """The libraries command publishes through the configured adapters."""
    source = tmp_path / "source"
    source.mkdir()
    archive_bytes = b"e2e library"
    (source / "Servo.zip").write_bytes(archive_bytes)
    checksum = hashlib.sha256(archive_bytes).hexdigest()
    target = tmp_path / "target"

    with _http_root(source) as origin:
        (source / "library_index.json").write_text(
            json.dumps(
                {
                    "libraries": [
                        {
                            "name": "Servo",
                            "version": "1.0.0",
                            "url": f"{origin}/Servo.zip",
                            "checksum": f"SHA-256:{checksum}",
                            "size": len(archive_bytes),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = main(
            [
                "libraries",
                "--log-level",
                "DEBUG",
                "--input",
                f"{origin}/library_index.json",
                "--mirror-host",
                "https://mirror.test.invalid",
                "--target",
                "local",
                "--local-root",
                str(target),
            ]
        )

    assert result == 0
    assert (
        capsys.readouterr().out
        == "libraries: published 1 release(s), 1 archive(s), 0 stale\n"
    )
    assert (target / "libraries" / "Servo.zip").read_bytes() == archive_bytes
    published = json.loads((target / "library_index.json").read_text(encoding="utf-8"))
    assert (
        published["libraries"][0]["url"]
        == "https://mirror.test.invalid/libraries/Servo.zip"
    )
    assert [
        record.getMessage() for record in log_records if record.levelno == logging.DEBUG
    ] == [
        "CLI_CONFIG_RESOLVED",
        "PUBLICATION_PIPELINE_COMPOSED",
        "PUBLICATION_RUN_REQUESTED",
        "SOURCE_REQUESTED",
        "SOURCE_RECEIVED",
        "SOURCE_FETCHED",
        "PLAN_SELECTED",
        "TARGET_ARCHIVE_KEYS_LISTED",
        "STALE_PLANNED",
        "ARCHIVE_DOWNLOAD_STARTED",
        "ARCHIVE_VERIFIED",
        "TARGET_ARCHIVES_PUBLISHED",
        "ARCHIVES_PUBLISHED",
        "TARGET_INDEX_REPLACED",
        "INDEX_REPLACED",
        "TARGET_STALE_CLEANED",
        "STALE_CLEANED",
    ]
    assert [extra_fields(record) for record in log_records[:3]] == [
        {
            "dry_run": False,
            "family": IndexFamily.LIBRARIES,
            "target": "local",
        },
        {"family": IndexFamily.LIBRARIES, "target": "local"},
        {"family": IndexFamily.LIBRARIES},
    ]


# endregion FUNC_test_libraries_cli_publishes_verified_archive_and_index


# region FUNC_test_libraries_cli_reports_progress_without_debug
# PURPOSE: Verify an operator sees meaningful publication progress without enabling diagnostic traces.
def test_libraries_cli_reports_progress_without_debug(
    tmp_path: Path, capsys, log_records
) -> None:
    """Normal CLI verbosity reports the live publication stages."""
    source = tmp_path / "source"
    source.mkdir()
    archive_bytes = b"progress library"
    (source / "Servo.zip").write_bytes(archive_bytes)
    checksum = hashlib.sha256(archive_bytes).hexdigest()
    target = tmp_path / "target"

    with _http_root(source) as origin:
        (source / "library_index.json").write_text(
            json.dumps(
                {
                    "libraries": [
                        {
                            "name": "Servo",
                            "version": "1.0.0",
                            "url": f"{origin}/Servo.zip",
                            "checksum": f"SHA-256:{checksum}",
                            "size": len(archive_bytes),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = main(
            [
                "libraries",
                "--input",
                f"{origin}/library_index.json",
                "--mirror-host",
                "https://mirror.test.invalid",
                "--target",
                "local",
                "--local-root",
                str(target),
            ]
        )

    assert result == 0
    captured = capsys.readouterr()
    assert captured.out == "libraries: published 1 release(s), 1 archive(s), 0 stale\n"
    assert "INFO arduino_mirror" in captured.err
    for progress in (
        "Fetching libraries index",
        "Selected 1 libraries release(s), 1 archive(s)",
        "Downloading libraries/Servo.zip",
        "Published libraries/Servo.zip",
        "Published library_index.json",
    ):
        assert progress in captured.err
    assert [record.getMessage() for record in log_records] == [
        "Fetching libraries index",
        "Fetched libraries index",
        "Selected 1 libraries release(s), 1 archive(s)",
        "Found 0 stale libraries archive(s)",
        "Downloading libraries/Servo.zip",
        "Verified libraries/Servo.zip (16 bytes)",
        "Published libraries/Servo.zip",
        "Published library_index.json",
    ]


# endregion FUNC_test_libraries_cli_reports_progress_without_debug


# region FUNC_test_libraries_cli_reports_nonzero_status_after_sigterm
# PURPOSE: Verify SIGTERM at a publication boundary leaves the CLI with an operator-visible nonzero result.
def test_libraries_cli_reports_nonzero_status_after_sigterm(
    monkeypatch, capsys
) -> None:
    """The temporary SIGTERM handler cancels the command without propagating a signal exception."""

    def interrupted_run(*_, cancellation, **__) -> None:
        os.kill(os.getpid(), signal.SIGTERM)
        cancellation.check()

    monkeypatch.setattr(
        "arduino_mirror.entrypoints.cli.run_publication", interrupted_run
    )

    result = main(["libraries", "--target", "local"])

    assert result == _SIGTERM_EXIT
    assert "Publication cancelled by SIGTERM" in capsys.readouterr().err


# endregion FUNC_test_libraries_cli_reports_nonzero_status_after_sigterm
