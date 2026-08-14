import asyncio

from repo_version_monitor.providers.gitlab import GitLabClient


class _FakeResponse:
    def __init__(self, payload: list) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> list:
        return self._payload


class _FakeClient:
    def __init__(self, pages: list[list]) -> None:
        self.pages = pages
        self.requests: list[dict] = []

    async def get(self, url, headers=None, params=None):
        self.requests.append({"url": url, "headers": headers or {}, "params": params or {}})
        page = (params or {}).get("page", 1)
        payload = self.pages[page - 1] if page <= len(self.pages) else []
        return _FakeResponse(payload)


def _tag_item(name: str, sha: str) -> dict:
    return {"name": name, "target": sha, "commit": {"id": sha}}


def test_fetch_url_encodes_project_path() -> None:
    gitlab = GitLabClient()
    client = _FakeClient([[_tag_item("v1.0.0", "sha1")]])

    tags = asyncio.run(gitlab.fetch_all_tags(client, "gitlab-org/gitlab-runner"))

    assert client.requests[0]["url"] == (
        "https://gitlab.com/api/v4/projects/gitlab-org%2Fgitlab-runner/repository/tags"
    )
    assert [tag.name for tag in tags] == ["v1.0.0"]
    assert tags[0].commit_sha == "sha1"


def test_fetch_supports_nested_subgroups_and_custom_base_url() -> None:
    gitlab = GitLabClient(base_url="https://gitlab.example.com/")
    client = _FakeClient([[]])

    asyncio.run(gitlab.fetch_all_tags(client, "group/subgroup/project"))

    assert client.requests[0]["url"] == (
        "https://gitlab.example.com/api/v4/projects/group%2Fsubgroup%2Fproject/repository/tags"
    )


def test_fetch_sends_private_token_header_when_configured() -> None:
    client = _FakeClient([[]])

    asyncio.run(GitLabClient(token="secret").fetch_all_tags(client, "a/b"))
    asyncio.run(GitLabClient().fetch_all_tags(client, "a/b"))

    assert client.requests[0]["headers"]["PRIVATE-TOKEN"] == "secret"
    assert "PRIVATE-TOKEN" not in client.requests[1]["headers"]


def test_fetch_paginates_until_short_page() -> None:
    first_page = [_tag_item(f"v1.0.{i}", f"sha{i}") for i in range(100)]
    second_page = [_tag_item("v0.9.0", "old-sha")]
    gitlab = GitLabClient()
    client = _FakeClient([first_page, second_page])

    tags = asyncio.run(gitlab.fetch_all_tags(client, "a/b"))

    assert len(tags) == 101
    assert [request["params"]["page"] for request in client.requests] == [1, 2]
    assert client.requests[0]["params"]["order_by"] == "updated"
    assert tags[-1].name == "v0.9.0"


def test_fetch_falls_back_to_target_when_commit_missing() -> None:
    gitlab = GitLabClient()
    client = _FakeClient([[{"name": "v1.0.0", "target": "tag-target", "commit": None}]])

    tags = asyncio.run(gitlab.fetch_all_tags(client, "a/b"))

    assert tags[0].commit_sha == "tag-target"
