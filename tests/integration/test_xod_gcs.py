# region MODULE_CONTRACT
# PURPOSE: Exercise installed rclone ADC exchange and XOD GCS destination offline.
# SCOPE: Real binary, synthetic external-account ADC, loopback STS/listing, dry-run.
# NOT: GitHub OIDC, production IAM, uploads, ACL enforcement, or APT version parity.
# KEYWORDS: integration, rclone, GCS, ADC, XOD, offline
# endregion MODULE_CONTRACT

"""Optional real-rclone smoke; no credentials or external services are used."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest

__all__: list[str] = []


# region FUNC_gcs_sts
# PURPOSE: Keep the synthetic ADC exchange and storage reads entirely on loopback.
@pytest.fixture
def gcs_sts():
    """Record token exchange and storage reads, rejecting every other request."""
    requests: list[tuple[str, str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, payload: object) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            form = parse_qs(
                self.rfile.read(int(self.headers["Content-Length"])).decode()
            )
            requests.append(("POST", self.path, form))
            if self.path == "/token":
                self._reply(
                    200,
                    {
                        "access_token": "offline-token",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            else:
                self._reply(403, {})

        def do_GET(self) -> None:
            requests.append(("GET", self.path, self.headers.get("Authorization")))
            if urlsplit(self.path).path == "/storage/v1/b/amperka-arduino-downloads/o":
                self._reply(200, {"items": []})
            else:
                self._reply(404, {"error": {"code": 404, "message": "not found"}})

        def do_CONNECT(self) -> None:
            requests.append(("CONNECT", self.path, None))
            self._reply(403, {})

        def do_DELETE(self) -> None:
            requests.append(("DELETE", self.path, None))
            self._reply(403, {})

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Silence expected local HTTP traffic."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


# endregion FUNC_gcs_sts


# region FUNC_test_xod_gcs_dry_run_uses_external_account_adc_and_owned_prefix
# PURPOSE: Prove real ADC token exchange/listing and XOD prefix selection without mutations.
def test_xod_gcs_dry_run_uses_external_account_adc_and_owned_prefix(
    tmp_path: Path, gcs_sts
) -> None:
    rclone = shutil.which("rclone")
    if rclone is None:
        pytest.skip("optional real-rclone smoke requires rclone on PATH")
    if Path(rclone).parent.name == "shims":
        pytest.skip(
            "put the real rclone binary on PATH; isolated smoke cannot use user-configured shims"
        )
    endpoint, requests = gcs_sts
    source = tmp_path / "dist"
    source.mkdir()
    (source / "archive.zip").write_bytes(b"offline archive")
    subject = tmp_path / "subject-token"
    subject.write_text("offline-subject", encoding="utf-8")
    credentials = tmp_path / "adc.json"
    credentials.write_text(
        json.dumps(
            {
                "type": "external_account",
                "audience": "//iam.googleapis.com/projects/123/locations/global/workloadIdentityPools/offline/providers/offline",
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "token_url": f"{endpoint}/token",
                "credential_source": {"file": str(subject)},
            }
        ),
        encoding="utf-8",
    )
    environment = {
        "PATH": os.defpath,
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path),
        "RCLONE_CONFIG": str(tmp_path / "empty.conf"),
        "GOOGLE_APPLICATION_CREDENTIALS": str(credentials),
        "RCLONE_CONFIG_STORAGE_TYPE": "google cloud storage",
        "RCLONE_CONFIG_STORAGE_ENV_AUTH": "true",
        "RCLONE_CONFIG_STORAGE_BUCKET_POLICY_ONLY": "true",
        "RCLONE_CONFIG_STORAGE_NO_CHECK_BUCKET": "true",
        "RCLONE_CONFIG_STORAGE_ENDPOINT": f"{endpoint}/storage/v1/",
        "HTTP_PROXY": endpoint,
        "HTTPS_PROXY": endpoint,
        "NO_PROXY": "127.0.0.1",
    }
    result = subprocess.run(
        [
            rclone,
            "sync",
            str(source),
            "storage:amperka-arduino-downloads/xod-fix/",
            "--dry-run",
            "--log-level",
            "INFO",
            "--retries",
            "1",
            "--low-level-retries",
            "1",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if "invalid TokenURL" in result.stderr:
        pytest.skip(
            "this rclone OAuth library rejects loopback STS URLs; ADC compatibility is source-verified separately"
        )
    assert result.returncode == 0, result.stderr
    exchanges = [
        payload
        for method, path, payload in requests
        if (method, path) == ("POST", "/token")
    ]
    assert exchanges
    exchange = exchanges[0]
    assert isinstance(exchange, dict)
    assert exchange["subject_token"] == ["offline-subject"]
    assert exchange["grant_type"] == ["urn:ietf:params:oauth:grant-type:token-exchange"]
    listings = [
        urlsplit(path)
        for method, path, _ in requests
        if method == "GET" and urlsplit(path).path.endswith("/o")
    ]
    assert listings
    assert all(
        parse_qs(listing.query)["prefix"] == ["xod-fix/"] for listing in listings
    )
    assert all(
        method == "GET" or (method, path) == ("POST", "/token")
        for method, path, _ in requests
    )
    assert all(
        auth == "Bearer offline-token"
        for method, _, auth in requests
        if method == "GET"
    )
    assert "archive.zip" in result.stderr
    assert "dry-run" in result.stderr


# endregion FUNC_test_xod_gcs_dry_run_uses_external_account_adc_and_owned_prefix
