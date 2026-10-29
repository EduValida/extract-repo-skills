"""Provider-neutral access to committed Git objects; never check out source code."""

import os
import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import pygit2

from .contracts import ValidationError
from .git_evidence import DIFF_OPTIONS


class AnalysisError(ValidationError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class TreeEntry:
    path: str
    oid: str
    mode: int
    size: int


class GitRepositoryReader(Protocol):
    version: dict

    def resolve(self, revision: str) -> str: ...
    def metadata(self, commit: str) -> dict: ...
    def history(self, commit: str, limit: int) -> list[dict]: ...
    def entries(self, commit: str, limit: int) -> list[TreeEntry]: ...
    def blob(self, entry: TreeEntry, max_bytes: int) -> bytes: ...
    def diff(self, commit: str, parents: list[str], context: int) -> str: ...
    def changed_paths(self, commit: str, parents: list[str]) -> list[str]: ...


def git_command(root: Path, *args: str) -> bytes:
    # Ignore caller Git overrides, global config and prompts. The remote URL is
    # validated separately; credentials may use the server's noninteractive SSH.
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_SSH_COMMAND="ssh -oBatchMode=yes",
    )
    options = [
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "protocol.ext.allow=never",
        "-c",
        "protocol.file.allow=never",
        "-c",
        "credential.helper=",
    ]
    try:
        result = subprocess.run(
            [
                "git",
                "--no-pager",
                "--no-replace-objects",
                "--literal-pathspecs",
                "-C",
                str(root),
                *options,
                *args,
            ],
            capture_output=True,
            timeout=30,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AnalysisError(
            "git_timeout", "Git operation exceeded 30 seconds", 504
        ) from exc
    except OSError as exc:
        raise AnalysisError("git_unavailable", "Cannot start Git", 503) from exc
    if result.returncode:
        # Git diagnostics may contain credentials, host paths or repository text.
        raise AnalysisError(
            "git_operation_failed", "Cannot retrieve the requested Git objects", 502
        )
    return result.stdout


class Pygit2Reader:
    """libgit2 objects with C Git's existing, explicitly configured diff policy."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.repo = pygit2.Repository(str(path))
        except (pygit2.GitError, ValueError) as exc:
            raise AnalysisError(
                "invalid_repository", "Cannot open this Git repository"
            ) from exc
        if self.repo.is_shallow:
            self.repo.free()
            raise AnalysisError(
                "incomplete_history",
                "A complete history is required; shallow repositories are unsupported",
            )
        self.version = {
            "objects": f"pygit2/{pygit2.__version__}",
            "libgit2": pygit2.LIBGIT2_VERSION,
            "diff": git_command(path, "--version").decode().strip(),
            "diff_policy": "git-myers-no-renames-v1",
        }

    def close(self):
        self.repo.free()

    def resolve(self, revision: str) -> str:
        try:
            return str(self.repo.revparse_single(revision).peel(pygit2.Commit).id)
        except (KeyError, ValueError, pygit2.GitError) as exc:
            raise AnalysisError(
                "invalid_ref", "Reference does not resolve to a commit"
            ) from exc

    def metadata(self, commit: str) -> dict:
        item = self.repo[commit].peel(pygit2.Commit)
        return {
            "commit": str(item.id),
            "parents": [str(oid) for oid in item.parent_ids],
            "author": {
                "name": item.author.name,
                "email": item.author.email,
                "timestamp": item.author.time,
                "offset_minutes": item.author.offset,
            },
            "message": item.message,
        }

    def history(self, commit: str, limit: int) -> list[dict]:
        result = []
        message_chars = 0
        try:
            for item in self.repo.walk(
                self.repo[commit].id, pygit2.enums.SortMode.TOPOLOGICAL
            ):
                if len(result) >= limit:
                    raise AnalysisError(
                        "history_limit",
                        f"Reachable history exceeds {limit} commits",
                        413,
                    )
                metadata = self.metadata(str(item.id))
                message_chars += len(metadata["message"])
                if message_chars > 5_000_000:
                    raise AnalysisError(
                        "history_limit",
                        "Commit metadata exceeds the history budget",
                        413,
                    )
                result.append(metadata)
        except (KeyError, pygit2.GitError) as exc:
            raise AnalysisError(
                "incomplete_history", "Some required Git objects are unavailable"
            ) from exc
        return result

    def entries(self, commit: str, limit: int) -> list[TreeEntry]:
        result = []

        def visit(tree, prefix=""):
            for item in tree:
                path = prefix + item.name
                if item.type == pygit2.enums.ObjectType.TREE:
                    visit(self.repo[item.id], path + "/")
                else:
                    if len(result) >= limit:
                        raise AnalysisError(
                            "tree_limit", f"Snapshot exceeds {limit} files", 413
                        )
                    size = (
                        self.repo[item.id].size
                        if item.type == pygit2.enums.ObjectType.BLOB
                        else 0
                    )
                    result.append(TreeEntry(path, str(item.id), item.filemode, size))

        visit(self.repo[commit].peel(pygit2.Commit).tree)
        return sorted(result, key=lambda entry: entry.path)

    def blob(self, entry: TreeEntry, max_bytes: int) -> bytes:
        if entry.mode not in (0o100644, 0o100755):
            raise AnalysisError(
                "unsupported_file", "Only regular file blobs can be read"
            )
        if entry.size > max_bytes:
            raise AnalysisError(
                "blob_limit", f"A required blob exceeds {max_bytes} bytes", 413
            )
        return self.repo[entry.oid].data

    def diff(self, commit: str, parents: list[str], context: int) -> str:
        if parents:
            args = [
                "diff",
                *DIFF_OPTIONS,
                "-p",
                f"--unified={context}",
                parents[0],
                commit,
                "--",
            ]
        else:
            args = [
                "diff-tree",
                "--root",
                "--no-commit-id",
                "-r",
                *DIFF_OPTIONS,
                "-p",
                f"--unified={context}",
                commit,
                "--",
            ]
        try:
            return git_command(self.path, *args).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AnalysisError(
                "unsupported_encoding", "Diff is not UTF-8 text"
            ) from exc

    def changed_paths(self, commit: str, parents: list[str]) -> list[str]:
        if parents:
            args = [
                "diff",
                *DIFF_OPTIONS,
                "--name-only",
                "-z",
                parents[0],
                commit,
                "--",
            ]
        else:
            args = [
                "diff-tree",
                "--root",
                "--no-commit-id",
                "-r",
                *DIFF_OPTIONS,
                "--name-only",
                "-z",
                commit,
                "--",
            ]
        try:
            return sorted(
                path
                for path in git_command(self.path, *args).decode("utf-8").split("\0")
                if path
            )
        except UnicodeDecodeError as exc:
            raise AnalysisError(
                "unsupported_encoding", "File paths must be UTF-8"
            ) from exc


def validate_remote(source: str, allowed_hosts: tuple[str, ...]) -> str:
    try:
        parsed = urlparse(source)
        hostname, port = parsed.hostname, parsed.port
    except ValueError as exc:
        raise AnalysisError("invalid_source", "Invalid Git remote URL") from exc
    if (
        parsed.scheme not in ("https", "ssh")
        or not hostname
        or not parsed.path
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "https" and parsed.username)
        or (
            parsed.username
            and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", parsed.username)
        )
        or port not in (None, 22 if parsed.scheme == "ssh" else 443)
    ):
        raise AnalysisError(
            "invalid_source", "Use an HTTPS or SSH Git URL without embedded credentials"
        )
    if hostname.lower() not in allowed_hosts:
        raise AnalysisError(
            "remote_host_not_allowed",
            "Remote host is not enabled in server configuration",
            403,
        )
    return source


@contextmanager
def open_repository(
    source: str,
    local_repositories: dict[str, str],
    allowed_hosts: tuple[str, ...],
    workspace: Path,
):
    if source.startswith("local:"):
        key = source.removeprefix("local:")
        if key not in local_repositories:
            raise AnalysisError(
                "unknown_repository", "Local repository is not registered", 404
            )
        path = Path(local_repositories[key])
    else:
        validate_remote(source, allowed_hosts)
        path = workspace / "repository.git"
        path.mkdir()
        git_command(path, "init", "--bare", "-q")
        # Fetch the advertised HEAD and branches, without tags, checkout, or submodules.
        git_command(
            path,
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-recurse-submodules",
            "--",
            source,
            "HEAD",
            "+refs/heads/*:refs/heads/*",
        )
        git_command(path, "update-ref", "HEAD", "FETCH_HEAD")
    reader = Pygit2Reader(path)
    try:
        yield reader
    finally:
        reader.close()
