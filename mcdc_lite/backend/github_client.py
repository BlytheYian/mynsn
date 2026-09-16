"""GitHub REST API 薄客戶端：用 OAuth Authorization Code flow 取得的 access token
瀏覽 repo／分支／檔案樹，並抓取單一 .py 檔案內容。Token 只在單次請求中夾帶，後端不儲存。"""
from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlencode

import httpx

GITHUB_API_BASE = "https://api.github.com"
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"

_TIMEOUT = httpx.Timeout(20.0, read=20.0)


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "repo",
        "state": state,
        "allow_signup": "false",
    }
    return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(
    client_id: str, client_secret: str, code: str, redirect_uri: str
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            GITHUB_ACCESS_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()


async def get_authenticated_user(token: str) -> dict[str, Any]:
    data = await _get("/user", token)
    return {"login": data["login"], "avatar_url": data["avatar_url"]}


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _get(path: str, token: str, params: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}{path}", headers=_headers(token), params=params
        )
        resp.raise_for_status()
        return resp.json()


async def list_repos(token: str) -> list[dict[str, Any]]:
    data = await _get(
        "/user/repos", token, params={"per_page": 100, "sort": "updated"}
    )
    return [
        {
            "full_name": r["full_name"],
            "owner": r["owner"]["login"],
            "name": r["name"],
            "private": r["private"],
            "default_branch": r["default_branch"],
        }
        for r in data
    ]


async def list_branches(token: str, owner: str, repo: str) -> list[str]:
    data = await _get(f"/repos/{owner}/{repo}/branches", token, params={"per_page": 100})
    return [b["name"] for b in data]


async def list_python_files(token: str, owner: str, repo: str, branch: str) -> list[str]:
    data = await _get(
        f"/repos/{owner}/{repo}/git/trees/{branch}", token, params={"recursive": 1}
    )
    tree = data.get("tree", [])
    return sorted(
        item["path"] for item in tree if item.get("type") == "blob" and item["path"].endswith(".py")
    )


async def get_file_content(token: str, owner: str, repo: str, path: str, branch: str) -> str:
    data = await _get(f"/repos/{owner}/{repo}/contents/{path}", token, params={"ref": branch})
    if data.get("encoding") != "base64":
        raise ValueError(f"unexpected encoding: {data.get('encoding')}")
    return base64.b64decode(data["content"]).decode("utf-8")
