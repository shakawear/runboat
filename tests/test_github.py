import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

from runboat import github
from runboat.github import GitHubStatusState
from runboat.settings import settings


class RecordingClient:
    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []

    async def __aenter__(self) -> "RecordingClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def request(
        self, method: str, url: str, *, headers: dict[str, str], json: Any
    ) -> httpx.Response:
        self.headers.append(headers)
        request = httpx.Request(method, url)
        return httpx.Response(200, json={}, request=request)


@pytest.mark.asyncio
async def test_github_request_rereads_token_file(
    tmp_path: Path, monkeypatch: MonkeyPatch, mocker: MockerFixture
) -> None:
    token_file = tmp_path / "github-token"
    token_file.write_text("first-token\n")
    monkeypatch.setattr(settings, "github_token_file", token_file)
    monkeypatch.setattr(settings, "github_token", "static-token")
    client = RecordingClient()
    mocker.patch("runboat.github.httpx.AsyncClient", return_value=client)

    await github._github_request("GET", "/first")
    token_file.write_text("second-token\n")
    await github._github_request("GET", "/second")

    assert [headers["Authorization"] for headers in client.headers] == [
        "Bearer first-token",
        "Bearer second-token",
    ]


@pytest.mark.asyncio
async def test_missing_token_file_does_not_break_status_notification(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(settings, "github_token_file", tmp_path / "missing")
    monkeypatch.setattr(settings, "disable_commit_statuses", False)
    client = RecordingClient()
    mocker.patch("runboat.github.httpx.AsyncClient", return_value=client)

    with caplog.at_level(logging.ERROR, logger="runboat.github"):
        await github.notify_status(
            "example/repository", "deadbeef", GitHubStatusState.pending, None
        )

    assert client.headers == []
    assert "Skipping GitHub commit status" in caplog.text
