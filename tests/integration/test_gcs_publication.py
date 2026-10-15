# region MODULE_CONTRACT
# PURPOSE: Verify native GCS publication through real HTTP origin downloads and real SDK requests to an isolated loopback Storage JSON API.
# SCOPE:
# - Archive/index/cleanup ordering, family and outer-prefix ownership, incremental reuse, and safe upload failure.
# - NOT: ADC/WIF exchange, IAM, live GCS atomicity, or large resumable uploads; the stub implements small multipart uploads only.
# KEYWORDS: integration, GCS, SDK, HTTP, publication, offline
# endregion MODULE_CONTRACT

"""Offline contract tests using the real GCS SDK, not a fake SDK client."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, unquote, urlsplit

import google.auth
import pytest
import requests

from arduino_mirror.domain import IndexFamily
from arduino_mirror.entrypoints.cli import run_publication
from arduino_mirror.entrypoints.config import Config
from tests.integration.test_local_publication import _http_root
from tests.log_assertions import extra_fields

__all__: list[str] = []

_BUCKET = "offline-contract-bucket"
_INDEX_KEY = "managed/l/libraries/library_index.json"
_ARCHIVE_KEY = "managed/l/Servo-1.0.0.zip"


@dataclass
class _StorageState:
    """Expose the local storage service's observable requests and object bytes."""

    objects: dict[str, bytes] = field(default_factory=dict)
    events: list[tuple[str, str]] = field(default_factory=list)
    failed_uploads: set[str] = field(default_factory=set)


@contextmanager
def _storage_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[_StorageState]:
    """Serve the small-object JSON API used by the unmodified real SDK client."""
    state = _StorageState()

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, status: int, payload: object | None = None) -> None:
            body = json.dumps(payload).encode() if payload is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path != f"/storage/v1/b/{_BUCKET}/o":
                self._respond(404, {"error": {"message": "unexpected object read"}})
                return
            prefix = parse_qs(parsed.query)["prefix"][0]
            state.events.append(("list", prefix))
            self._respond(
                200,
                {
                    "items": [
                        {"name": name, "size": str(len(data))}
                        for name, data in sorted(state.objects.items())
                        if name.startswith(prefix)
                    ],
                },
            )

        def do_POST(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path != f"/upload/storage/v1/b/{_BUCKET}/o" or parse_qs(
                parsed.query
            ).get("uploadType") != ["multipart"]:
                self._respond(400, {"error": {"message": "only multipart supported"}})
                return
            body = self.rfile.read(int(self.headers["Content-Length"]))
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + body
            )
            metadata_part, data_part = message.iter_parts()
            metadata_bytes = metadata_part.get_payload(decode=True)
            data = data_part.get_payload(decode=True)
            assert isinstance(metadata_bytes, bytes)
            assert isinstance(data, bytes)
            metadata = json.loads(metadata_bytes)
            name = metadata["name"]
            state.events.append(("upload", name))
            if name in state.failed_uploads:
                self._respond(403, {"error": {"message": "upload forbidden"}})
                return
            state.objects[name] = data
            self._respond(200, {**metadata, "size": str(len(data))})

        def do_DELETE(self) -> None:
            path = urlsplit(self.path).path
            prefix = f"/storage/v1/b/{_BUCKET}/o/"
            if not path.startswith(prefix):
                self._respond(404)
                return
            name = unquote(path.removeprefix(prefix))
            state.events.append(("delete", name))
            state.objects.pop(name, None)
            self._respond(204)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Keep expected test API traffic out of operator logging assertions."""

    def unexpected_adc(*_args, **_kwargs):
        pytest.fail("offline test attempted ADC discovery")

    request = requests.Session.request

    def loopback_request(self, method, url, *args, **kwargs):
        assert urlsplit(url).hostname == "127.0.0.1", "external HTTP is forbidden"
        return request(self, method, url, *args, **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    monkeypatch.setenv(
        "STORAGE_EMULATOR_HOST",
        f"http://127.0.0.1:{server.server_address[1]}",
    )
    monkeypatch.setattr(google.auth, "default", unexpected_adc)
    monkeypatch.setattr(requests.Session, "request", loopback_request)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _library_source(source: Path, origin: str, archive_bytes: bytes) -> str:
    """Write a checksum-bearing origin release and nested library index."""
    (source / "Servo-1.0.0.zip").write_bytes(archive_bytes)
    index = source / "libraries" / "library_index.json"
    index.parent.mkdir()
    index.write_text(
        json.dumps(
            {
                "libraries": [
                    {
                        "name": "Servo",
                        "version": "1.0.0",
                        "url": f"{origin}/Servo-1.0.0.zip",
                        "checksum": f"SHA-256:{hashlib.sha256(archive_bytes).hexdigest()}",
                        "size": len(archive_bytes),
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    return f"{origin}/libraries/library_index.json"


def _config(index_url: str) -> Config:
    return Config.from_values(
        family=IndexFamily.LIBRARIES,
        values={
            "input_index": index_url,
            "mirror_host": "https://mirror.test.invalid",
            "target": "gcs",
            "bucket": _BUCKET,
            "prefix": "managed",
            "retry_attempts": 2,
            "retry_base_delay": 0.0,
        },
        environment={},
    )


# region FUNC_test_gcs_publication_orders_writes_scopes_cleanup_and_reuses_archives
# PURPOSE: Check actual SDK request order and incremental behavior while preserving unrelated family and outer-prefix objects.
def test_gcs_publication_orders_writes_scopes_cleanup_and_reuses_archives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archive_bytes = b"verified GCS library release"
    protected = {
        "managed/p/protected.tar.bz2": b"package family",
        "other/l/protected.zip": b"other outer prefix",
        "managed/later/protected.zip": b"similar family prefix",
    }

    with _storage_api(monkeypatch) as storage, _http_root(source) as origin:
        storage.objects.update(protected)
        storage.objects[_INDEX_KEY] = b'{"state":"previous"}'
        storage.objects["managed/l/obsolete.zip"] = b"stale"
        index_url = _library_source(source, origin, archive_bytes)
        plan = run_publication(_config(index_url))

        assert plan.archive_keys == ("l/Servo-1.0.0.zip",)
        assert plan.stale_keys == ("l/obsolete.zip",)
        assert storage.events == [
            ("list", "managed/l/"),
            ("upload", _ARCHIVE_KEY),
            ("upload", _INDEX_KEY),
            ("delete", "managed/l/obsolete.zip"),
        ]
        assert storage.objects[_ARCHIVE_KEY] == archive_bytes
        index = json.loads(storage.objects[_INDEX_KEY])
        assert index["libraries"][0]["url"] == (
            "https://mirror.test.invalid/l/Servo-1.0.0.zip"
        )
        assert index["libraries"][0]["checksum"] == (
            f"SHA-256:{hashlib.sha256(archive_bytes).hexdigest()}"
        )
        assert "managed/l/obsolete.zip" not in storage.objects
        assert {key: storage.objects[key] for key in protected} == protected

        # A real second run must succeed even after the origin archive disappears.
        (source / "Servo-1.0.0.zip").unlink()
        storage.events.clear()
        repeated = run_publication(_config(index_url))

        assert repeated.archive_keys == plan.archive_keys
        assert repeated.archives_to_publish == ()
        assert storage.events == [
            ("list", "managed/l/"),
            ("upload", _INDEX_KEY),
        ]
        assert storage.objects[_ARCHIVE_KEY] == archive_bytes
        assert {key: storage.objects[key] for key in protected} == protected


# endregion FUNC_test_gcs_publication_orders_writes_scopes_cleanup_and_reuses_archives


# region FUNC_test_gcs_upload_failure_preserves_prior_publication_when_fallback_is_empty
# PURPOSE: Prove a real SDK 403 upload failure enters safe fallback without replacing the published index or cleaning old archives.
def test_gcs_upload_failure_preserves_prior_publication_when_fallback_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    previous = {
        _INDEX_KEY: b'{"state":"previous publication"}',
        "managed/l/previous.zip": b"previous archive",
        "managed/p/protected.tar.bz2": b"package family",
    }
    caplog.set_level("DEBUG", logger="arduino_mirror")

    with _storage_api(monkeypatch) as storage, _http_root(source) as origin:
        storage.objects.update(previous)
        storage.failed_uploads.add(_ARCHIVE_KEY)
        index_url = _library_source(source, origin, b"verified but upload denied")
        plan = run_publication(_config(index_url))

    assert plan.archives == ()
    assert storage.objects == previous
    assert storage.events == [
        ("list", "managed/l/"),
        ("upload", _ARCHIVE_KEY),
    ]
    fallback = next(
        record
        for record in caplog.records
        if record.getMessage() == "ARCHIVE_FALLBACK_SELECTED"
    )
    assert extra_fields(fallback) == {
        "family": IndexFamily.LIBRARIES,
        "archive_key": "l/Servo-1.0.0.zip",
    }


# endregion FUNC_test_gcs_upload_failure_preserves_prior_publication_when_fallback_is_empty
