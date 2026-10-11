from urllib.parse import quote


def runner_path(scope: str) -> str:
    if scope == "instance":
        return "/admin/actions/runners"
    kind, separator, owner = scope.partition(":")
    if separator and kind == "org":
        return f"/orgs/{quote(owner, safe='')}/actions/runners"
    if separator and kind == "user":
        return "/user/actions/runners"
    owner, repo = scope.split("/")
    return f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/actions/runners"


def validate_scope(scope: str) -> None:
    if not scope or scope == "instance":
        return
    kind, separator, owner = scope.partition(":")
    if (
        not separator
        or kind not in {"org", "user"}
        or not owner
        or any(c in owner for c in "/:")
        or any(c.isspace() for c in owner)
    ):
        raise ValueError("FORGEJO_SCOPE must be org:name, user:name, or instance")


def accepts_repo(repo: str, scope: str, repos: tuple[str, ...]) -> bool:
    parts = repo.split("/")
    if len(parts) != 2 or not all(parts):
        return False
    if repos and repo not in repos:
        return False
    if scope == "instance":
        return True
    if scope:
        return parts[0].casefold() == scope.partition(":")[2].casefold()
    return repo in repos
