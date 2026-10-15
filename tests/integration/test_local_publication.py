# region MODULE_CONTRACT
# PURPOSE: Prove the publication HTTP and local-storage adapters publish one verified library family without touching another family namespace.
# SCOPE:
# - Local HTTP source, local target delivery, integrity rejection, and index replacement boundary.
# - NOT: S3 adapter coverage or production implementation.
# KEYWORDS: integration test, HTTP, local target, archive verification, publication
# endregion MODULE_CONTRACT

"""Integration tests for the publication local publication path."""

from __future__ import annotations

import hashlib
import json
import logging
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from arduino_mirror.domain import IndexFamily
from arduino_mirror.entrypoints.cli import run_publication
from arduino_mirror.entrypoints.config import Config
from arduino_mirror.entrypoints.signals import PublicationCancelledError
from tests.log_assertions import extra_fields


@contextmanager
def _http_root(directory: Path) -> Iterator[str]:
    """Serve one temporary directory and yield its origin URL."""
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


def _config(index_url: str, target_root: Path) -> Config:
    """Create one explicit library local-target publication configuration."""
    return Config.from_values(
        family=IndexFamily.LIBRARIES,
        values={
            "input_index": index_url,
            "mirror_host": "https://mirror.test.invalid",
            "target": "local",
            "local_root": str(target_root),
        },
        environment={},
    )


# region FUNC_test_local_publication_flow_verifies_archives_replaces_index_and_cleans_own_prefix
# PURPOSE: Verify successful publication publication preserves unrelated namespaces while atomically exposing a transformed library index.
def test_local_publication_flow_verifies_archives_replaces_index_and_cleans_own_prefix(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A verified library archive publishes before its index and replaces only library stale keys."""
    caplog.set_level(logging.DEBUG, logger="arduino_mirror")
    source = tmp_path / "source"
    source.mkdir()
    archive_bytes = b"library release bytes"
    (source / "Servo-1.0.0.zip").write_bytes(archive_bytes)
    checksum = hashlib.sha256(archive_bytes).hexdigest()
    target = tmp_path / "target"
    (target / "l" / "libraries").mkdir(parents=True)
    (target / "l" / "obsolete.zip").write_bytes(b"old")
    (target / "l" / "libraries" / "library_index.json").write_text(
        '{"state": "old"}', encoding="utf-8"
    )
    (target / "p").mkdir()
    (target / "p" / "protected.tar.bz2").write_bytes(b"package")

    with _http_root(source) as origin:
        (source / "libraries").mkdir()
        (source / "libraries" / "library_index.json").write_text(
            json.dumps(
                {
                    "libraries": [
                        {
                            "name": "Servo",
                            "version": "1.0.0",
                            "url": f"{origin}/Servo-1.0.0.zip",
                            "checksum": f"SHA-256:{checksum}",
                            "size": len(archive_bytes),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        plan = run_publication(
            _config(f"{origin}/libraries/library_index.json", target)
        )

    assert plan.archive_keys == ("l/Servo-1.0.0.zip",)
    assert (target / "l" / "Servo-1.0.0.zip").read_bytes() == archive_bytes
    assert not (target / "l" / "obsolete.zip").exists()
    assert (target / "p" / "protected.tar.bz2").read_bytes() == b"package"
    index = json.loads(
        (target / "l" / "libraries" / "library_index.json").read_text(encoding="utf-8")
    )
    assert (
        index["libraries"][0]["url"] == "https://mirror.test.invalid/l/Servo-1.0.0.zip"
    )
    trace_records = [
        record for record in caplog.records if record.levelno == logging.DEBUG
    ]
    assert [record.getMessage() for record in trace_records] == [
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
    assert [extra_fields(record) for record in trace_records] == [
        {"family": IndexFamily.LIBRARIES, "target": "local"},
        {"family": IndexFamily.LIBRARIES},
        {"family": IndexFamily.LIBRARIES},
        {"family": IndexFamily.LIBRARIES},
        {"family": IndexFamily.LIBRARIES},
        {
            "archive_count": 1,
            "family": IndexFamily.LIBRARIES,
            "release_count": 1,
        },
        {"archive_count": 1, "family": IndexFamily.LIBRARIES},
        {
            "family": IndexFamily.LIBRARIES,
            "stale_count": 1,
            "to_publish_count": 1,
        },
        {
            "archive_key": "l/Servo-1.0.0.zip",
            "family": IndexFamily.LIBRARIES,
        },
        {
            "archive_key": "l/Servo-1.0.0.zip",
            "family": IndexFamily.LIBRARIES,
            "size": len(archive_bytes),
        },
        {"archive_count": 1, "family": IndexFamily.LIBRARIES},
        {"archive_count": 1, "family": IndexFamily.LIBRARIES},
        {
            "family": IndexFamily.LIBRARIES,
            "index_key": "l/libraries/library_index.json",
        },
        {"family": IndexFamily.LIBRARIES},
        {"family": IndexFamily.LIBRARIES, "stale_count": 1},
        {"family": IndexFamily.LIBRARIES, "stale_count": 1},
    ]


# endregion FUNC_test_local_publication_flow_verifies_archives_replaces_index_and_cleans_own_prefix


# region FUNC_test_local_overlay_publishes_origin_archives_and_preserves_external_urls
# PURPOSE: Verify a local library overlay follows existing origin ownership during real HTTP and local-target publication.
def test_local_overlay_publishes_origin_archives_and_preserves_external_urls(
    tmp_path: Path,
) -> None:
    """An overlay-origin library publishes while an external overlay library stays external."""
    source = tmp_path / "source"
    source.mkdir()
    archive_bytes = b"overlay library"
    (source / "Overlay.zip").write_bytes(archive_bytes)
    target = tmp_path / "target"
    overlay = tmp_path / "libraries-overlay.json"

    with _http_root(source) as origin:
        (source / "library_index.json").write_text(
            json.dumps({"libraries": []}), encoding="utf-8"
        )
        overlay.write_text(
            json.dumps(
                {
                    "libraries": [
                        {
                            "name": "Overlay",
                            "version": "1.0.0",
                            "url": f"{origin}/Overlay.zip",
                            "size": len(archive_bytes),
                        },
                        {
                            "name": "External",
                            "version": "1.0.0",
                            "url": "https://vendor.example.invalid/External.zip",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        plan = run_publication(
            replace(
                _config(f"{origin}/library_index.json", target), local_index=overlay
            )
        )

    assert plan.archive_keys == ("l/Overlay.zip",)
    assert (target / "l" / "Overlay.zip").read_bytes() == archive_bytes
    index = json.loads(
        (target / "l" / "library_index.json").read_text(encoding="utf-8")
    )
    libraries = {library["name"]: library for library in index["libraries"]}
    assert libraries["Overlay"]["url"] == "https://mirror.test.invalid/l/Overlay.zip"
    assert libraries["External"]["url"] == "https://vendor.example.invalid/External.zip"


# endregion FUNC_test_local_overlay_publishes_origin_archives_and_preserves_external_urls


# region FUNC_test_local_publication_skips_matching_archive
# PURPOSE: Verify a repeated publication does not download or copy a local archive whose selected key and declared size already match.
def test_local_publication_skips_matching_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A confirmed local archive is retained while the family index still publishes."""
    caplog.set_level(logging.INFO, logger="arduino_mirror")
    source = tmp_path / "source"
    source.mkdir()
    archive_bytes = b"existing library release"
    target = tmp_path / "target"
    existing = target / "l" / "Existing-1.0.0.zip"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(archive_bytes)

    with _http_root(source) as origin:
        (source / "library_index.json").write_text(
            json.dumps(
                {
                    "libraries": [
                        {
                            "name": "Existing",
                            "version": "1.0.0",
                            "url": f"{origin}/Existing-1.0.0.zip",
                            "size": len(archive_bytes),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "arduino_mirror.infra.local_target.download_verified",
            lambda *_: pytest.fail("already-published archive was downloaded"),
        )
        run_publication(_config(f"{origin}/library_index.json", target))

    assert existing.read_bytes() == archive_bytes
    assert (target / "l" / "library_index.json").is_file()


# endregion FUNC_test_local_publication_skips_matching_archive


# region FUNC_test_local_publication_replaces_same_size_checksum_mismatch
# PURPOSE: Verify reconciliation replaces local bytes that have the declared size but fail supplied SHA-256 metadata.
def test_local_publication_replaces_same_size_checksum_mismatch(
    tmp_path: Path,
) -> None:
    """A same-size corrupted local archive is re-downloaded before index replacement."""
    source = tmp_path / "source"
    source.mkdir()
    archive_bytes = b"valid-release"
    (source / "Existing-1.0.0.zip").write_bytes(archive_bytes)
    target = tmp_path / "target"
    corrupt_bytes = b"corrupt-bytes"
    assert len(corrupt_bytes) == len(archive_bytes)
    existing = target / "l" / "Existing-1.0.0.zip"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(corrupt_bytes)

    with _http_root(source) as origin:
        (source / "library_index.json").write_text(
            json.dumps(
                {
                    "libraries": [
                        {
                            "name": "Existing",
                            "version": "1.0.0",
                            "url": f"{origin}/Existing-1.0.0.zip",
                            "checksum": (
                                f"SHA-256:{hashlib.sha256(archive_bytes).hexdigest()}"
                            ),
                            "size": len(archive_bytes),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        run_publication(_config(f"{origin}/library_index.json", target))

    assert existing.read_bytes() == archive_bytes


# endregion FUNC_test_local_publication_replaces_same_size_checksum_mismatch


# region FUNC_test_invalid_archive_does_not_replace_index
# PURPOSE: Verify failed supplied integrity metadata excludes the archive and preserves the current family index when no fallback exists.
def test_invalid_archive_does_not_replace_index(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A size mismatch leaves the local family index absent after safely exhausting candidates."""
    caplog.set_level(logging.DEBUG, logger="arduino_mirror")
    source = tmp_path / "source"
    source.mkdir()
    (source / "broken.zip").write_bytes(b"actual")
    target = tmp_path / "target"

    with _http_root(source) as origin:
        (source / "library_index.json").write_text(
            json.dumps(
                {
                    "libraries": [
                        {
                            "name": "Broken",
                            "version": "1.0.0",
                            "url": f"{origin}/broken.zip",
                            "size": 99,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        plan = run_publication(_config(f"{origin}/library_index.json", target))

    assert plan.archives == ()
    assert not (target / "l" / "broken.zip").exists()
    assert not (target / "l" / "library_index.json").exists()
    verification = next(
        record
        for record in caplog.records
        if record.getMessage() == "ARCHIVE_VERIFICATION_FAILED"
    )
    assert extra_fields(verification) == {
        "archive_key": "l/broken.zip",
        "check": "size",
        "family": IndexFamily.LIBRARIES,
    }
    fallback = next(
        record
        for record in caplog.records
        if record.getMessage() == "ARCHIVE_FALLBACK_SELECTED"
    )
    assert extra_fields(fallback) == {
        "archive_key": "l/broken.zip",
        "family": IndexFamily.LIBRARIES,
    }


# endregion FUNC_test_invalid_archive_does_not_replace_index


# region FUNC_test_cancellation_after_archive_preserves_index_and_stale_files
# PURPOSE: Verify a real local publication stops at the archive boundary without replacing its prior index or deleting stale archives.
def test_cancellation_after_archive_preserves_index_and_stale_files(
    tmp_path: Path,
) -> None:
    """SIGTERM-equivalent cancellation leaves the visible local family state intact."""
    source = tmp_path / "source"
    source.mkdir()
    archive_bytes = b"cancelled library"
    (source / "Servo.zip").write_bytes(archive_bytes)
    target = tmp_path / "target"
    stale = target / "l" / "obsolete.zip"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    previous_index = target / "l" / "library_index.json"
    previous_index.write_text('{"state": "old"}', encoding="utf-8")

    with _http_root(source) as origin:
        (source / "library_index.json").write_text(
            json.dumps(
                {
                    "libraries": [
                        {
                            "name": "Servo",
                            "version": "1.0.0",
                            "url": f"{origin}/Servo.zip",
                            "size": len(archive_bytes),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        def check_cancelled() -> None:
            if (target / "l" / "Servo.zip").is_file():
                raise PublicationCancelledError(signal.SIGTERM)

        with pytest.raises(PublicationCancelledError):
            run_publication(
                _config(f"{origin}/library_index.json", target),
                check_cancelled=check_cancelled,
            )

    assert (target / "l" / "Servo.zip").read_bytes() == archive_bytes
    assert stale.read_bytes() == b"old"
    assert previous_index.read_text(encoding="utf-8") == '{"state": "old"}'


# endregion FUNC_test_cancellation_after_archive_preserves_index_and_stale_files


# region FUNC_test_publication_dry_run_does_not_create_target
# PURPOSE: Verify publication dry run fetches and transforms the index without listing, writing, or creating its configured target.
def test_publication_dry_run_does_not_create_target(tmp_path: Path) -> None:
    """A publication preview does not require the selected archive or local target to exist."""
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target"

    with _http_root(source) as origin:
        (source / "library_index.json").write_text(
            json.dumps(
                {
                    "libraries": [
                        {
                            "name": "Preview",
                            "version": "1.0.0",
                            "url": f"{origin}/not-fetched.zip",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        plan = run_publication(
            replace(
                _config(f"{origin}/library_index.json", target),
                dry_run=True,
            )
        )

    assert plan.archive_keys == ("l/not-fetched.zip",)
    assert not target.exists()


# endregion FUNC_test_publication_dry_run_does_not_create_target
