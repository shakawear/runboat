import logging
from enum import StrEnum
from typing import Any

import httpx
from pydantic import BaseModel, field_validator

from .exceptions import NotFoundOnGitHub
from .settings import settings

_logger = logging.getLogger(__name__)


class GitHubTokenUnavailable(RuntimeError):
    """The configured dynamic GitHub token cannot currently be read."""


def _github_token() -> str | None:
    if settings.github_token_file is None:
        return settings.github_token
    try:
        token = settings.github_token_file.read_text().strip()
    except OSError as error:
        raise GitHubTokenUnavailable(
            f"Could not read GitHub token file {settings.github_token_file}"
        ) from error
    if not token:
        raise GitHubTokenUnavailable(
            f"GitHub token file {settings.github_token_file} is empty"
        )
    return token


async def _github_request(method: str, url: str, json: Any = None) -> Any:
    async with httpx.AsyncClient() as client:
        full_url = f"https://api.github.com{url}"
        headers = {
            "Accept": "application/vnd.github.v3+json",
        }
        if token := _github_token():
            headers["Authorization"] = f"token {token}"
        response = await client.request(method, full_url, headers=headers, json=json)
        if response.status_code == 404:
            raise NotFoundOnGitHub(f"GitHub URL not found: {full_url}.")
        response.raise_for_status()
        return response.json()


class CommitInfo(BaseModel):
    repo: str
    target_branch: str
    pr: int | None
    git_commit: str

    @field_validator("repo")
    def validate_repo(cls, v: str) -> str:
        return v.lower()


async def get_branch_info(repo: str, branch: str) -> CommitInfo:
    branch_data = await _github_request("GET", f"/repos/{repo}/git/ref/heads/{branch}")
    return CommitInfo(
        repo=repo,
        target_branch=branch,
        pr=None,
        git_commit=branch_data["object"]["sha"],
    )


async def get_pull_info(repo: str, pr: int) -> CommitInfo:
    pr_data = await _github_request("GET", f"/repos/{repo}/pulls/{pr}")
    return CommitInfo(
        repo=repo,
        target_branch=pr_data["base"]["ref"],
        pr=pr,
        git_commit=pr_data["head"]["sha"],
    )


class GitHubStatusState(StrEnum):
    error = "error"
    failure = "failure"
    pending = "pending"
    success = "success"


async def notify_status(
    repo: str, sha: str, state: GitHubStatusState, target_url: str | None
) -> None:
    if settings.disable_commit_statuses:
        return
    # https://docs.github.com/en/rest/reference/repos#create-a-commit-status
    try:
        await _github_request(
            "POST",
            f"/repos/{repo}/statuses/{sha}",
            json={
                "state": state,
                "target_url": target_url,
                "context": "runboat/build",
            },
        )
    except httpx.HTTPStatusError as e:
        _logger.error(
            f"Failed to post GitHub commit status (code {e.response.status_code}):\n"
            f"{e.response.text}"
        )
    except GitHubTokenUnavailable as error:
        # Status reporting must not interrupt build lifecycle operations. Metadata
        # lookups still fail when their configured private-repository token is absent.
        _logger.error("Skipping GitHub commit status: %s", error)
