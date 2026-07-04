import os
import subprocess
import time
from dataclasses import dataclass

from worktree_manager.models import WorktreeModel


@dataclass
class UpstreamStatus:
    has_upstream: bool
    ahead: int
    behind: int


@dataclass
class PullOutcome:
    status: str   # "up_to_date" | "pulled" | "non_ff" | "dirty" | "error"
    new_commits: int = 0
    error: str | None = None


@dataclass
class UpdateOutcome:
    status: str   # "up_to_date" | "pulled" | "non_ff" | "error"
    error: str | None = None


class GitService:
    def _run(self, cmd: list, cwd: str = None) -> str:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, check=True
        )
        return result.stdout

    def _run_input(self, cmd: list, cwd: str, input: str = None, check: bool = True) -> str:
        result = subprocess.run(
            cmd, cwd=cwd, input=input, capture_output=True, text=True, check=check
        )
        return result.stdout

    def is_valid_repo(self, path: str) -> bool:
        return os.path.isdir(os.path.join(path, ".git"))

    def toplevel_for(self, cwd: str) -> str | None:
        try:
            return self._run(["git", "rev-parse", "--show-toplevel"], cwd=cwd).strip()
        except subprocess.CalledProcessError:
            return None

    def ref_exists(self, repo_path: str, ref: str) -> bool:
        """True if git can resolve ``ref`` to a commit in ``repo_path``."""
        try:
            self._run(
                ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                cwd=repo_path,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def last_commit_ts(self, repo_path: str, branch: str) -> int:
        try:
            out = self._run(
                ["git", "log", "-1", "--format=%ct", branch], cwd=repo_path
            ).strip()
            return int(out) if out else 0
        except subprocess.CalledProcessError:
            return 0

    def is_merged(self, repo_path: str, branch: str, main_branch: str) -> bool:
        out = self._run(
            ["git", "branch", "--merged", main_branch], cwd=repo_path
        )
        merged = [b.strip().lstrip("* ") for b in out.splitlines()]
        return branch in merged

    def list_local_branches(self, repo_path: str) -> list:
        out = self._run(["git", "branch", "--format=%(refname:short)"], cwd=repo_path)
        return [b for b in out.splitlines() if b]

    def list_remote_branches(self, repo_path: str) -> list[str]:
        try:
            out = self._run(["git", "branch", "-r"], cwd=repo_path)
        except Exception:
            return []
        branches = []
        for line in out.splitlines():
            name = line.strip()
            if "->" in name:
                continue
            # strip "origin/" prefix
            if "/" in name:
                name = name.split("/", 1)[1]
            if name:
                branches.append(name)
        return branches

    def list_worktrees(self, repo_path: str, stale_days: int = 30) -> list:
        out = self._run(["git", "worktree", "list", "--porcelain"], cwd=repo_path)
        blocks = [b.strip() for b in out.strip().split("\n\n") if b.strip()]
        stale_threshold = int(time.time()) - stale_days * 86400
        worktrees = []
        for i, block in enumerate(blocks):
            lines = {
                k: v
                for k, v in (
                    line.split(" ", 1) for line in block.splitlines() if " " in line
                )
            }
            path = lines.get("worktree", "")
            branch_ref = lines.get("branch", "")
            branch = branch_ref.removeprefix("refs/heads/") if branch_ref else "(detached)"
            is_main = i == 0
            ts = self.last_commit_ts(repo_path, branch)
            merged = self.is_merged(repo_path, branch, "main") if not is_main else False
            stale = ts > 0 and ts < stale_threshold
            worktrees.append(WorktreeModel(
                path=path,
                branch=branch,
                is_main=is_main,
                last_commit_ts=ts,
                is_merged=merged,
                is_stale=stale,
            ))
        return worktrees

    def create_worktree(
        self, repo_path: str, worktree_path: str, branch: str, base_branch: str
    ) -> None:
        self._run(
            ["git", "worktree", "add", "-b", branch, worktree_path, base_branch],
            cwd=repo_path,
        )

    def delete_worktree(self, repo_path: str, worktree_path: str) -> None:
        self._run(
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=repo_path,
        )

    def delete_branch(self, repo_path: str, branch: str) -> None:
        self._run(["git", "branch", "-D", branch], cwd=repo_path)

    def has_uncommitted_changes(self, worktree_path: str) -> bool:
        try:
            out = self._run(["git", "status", "--porcelain"], cwd=worktree_path)
            return bool(out.strip())
        except subprocess.CalledProcessError:
            return False

    def checked_out_branch(self, worktree_path: str) -> str:
        try:
            result = self._run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_path
            ).strip()
            return "(detached)" if result == "HEAD" else result
        except subprocess.CalledProcessError:
            return "(detached)"

    def checkout_branch(self, worktree_path: str, branch: str) -> None:
        self._run(["git", "checkout", branch], cwd=worktree_path)

    def checkout_new_branch(self, worktree_path: str, new_branch: str, base_branch: str) -> None:
        if base_branch == "HEAD":
            self._run(["git", "checkout", "-b", new_branch], cwd=worktree_path)
        else:
            self._run(["git", "checkout", "-b", new_branch, base_branch], cwd=worktree_path)

    def create_worktree_from_existing(
        self, repo_path: str, worktree_path: str, branch: str
    ) -> None:
        self._run(
            ["git", "worktree", "add", worktree_path, branch],
            cwd=repo_path,
        )

    def repo_root(self, worktree_path: str) -> str:
        return self._run(
            ["git", "rev-parse", "--show-toplevel"], cwd=worktree_path
        ).strip()

    def list_feature_branches(self, repo_path: str) -> list[str]:
        out = self._run(["git", "branch", "--format=%(refname:short)"], cwd=repo_path)
        return [b for b in out.splitlines() if b.startswith("feature/")]

    def is_merged_into_any(
        self, repo_path: str, branch: str, targets: list[str]
    ) -> tuple[bool, str | None]:
        for target in targets:
            out = self._run(["git", "branch", "--merged", target], cwd=repo_path)
            merged = [b.strip().lstrip("* ") for b in out.splitlines()]
            if branch in merged:
                return True, target
        return False, None

    def build_merged_map(self, repo_path: str, targets: list[str]) -> dict[str, str]:
        target_set = set(targets)
        result: dict[str, str] = {}
        for target in targets:
            out = self._run(["git", "branch", "--merged", target], cwd=repo_path)
            for line in out.splitlines():
                branch = line.strip().lstrip("* ")
                if branch and branch not in result and branch not in target_set:
                    result[branch] = target
        return result

    # ── sync methods ──────────────────────────────────────────────────────────

    def fetch(self, repo_path: str) -> str | None:
        """Fetch from origin. Returns None on success, error string on failure."""
        try:
            self._run(["git", "fetch", "origin"], cwd=repo_path)
            return None
        except subprocess.CalledProcessError as e:
            return e.stderr or str(e)

    def upstream_status(self, repo_path: str, branch: str) -> UpstreamStatus:
        """Return ahead/behind counts vs the tracking branch, or has_upstream=False."""
        try:
            out = self._run(
                ["git", "rev-list", "--left-right", "--count",
                 f"{branch}...@{{u}}"],
                cwd=repo_path,
            ).strip()
            ahead_str, behind_str = out.split()
            return UpstreamStatus(
                has_upstream=True, ahead=int(ahead_str), behind=int(behind_str)
            )
        except (subprocess.CalledProcessError, ValueError):
            return UpstreamStatus(has_upstream=False, ahead=0, behind=0)

    def list_feature_and_main_branches(self, repo_path: str) -> list[str]:
        """Return local branches that are main or start with feature/."""
        out = self._run(["git", "branch", "--format=%(refname:short)"], cwd=repo_path)
        return [
            b for b in out.splitlines()
            if b == "main" or b.startswith("feature/")
        ]

    def worktree_for_branch(self, repo_path: str, branch: str) -> str | None:
        """Return the worktree path that has branch checked out, or None."""
        out = self._run(["git", "worktree", "list", "--porcelain"], cwd=repo_path)
        blocks = [b.strip() for b in out.strip().split("\n\n") if b.strip()]
        for block in blocks:
            lines = {
                k: v
                for k, v in (
                    line.split(" ", 1) for line in block.splitlines() if " " in line
                )
            }
            branch_ref = lines.get("branch", "")
            wt_branch = branch_ref.removeprefix("refs/heads/")
            if wt_branch == branch:
                return lines.get("worktree")
        return None

    def pull_ff_only(self, worktree_path: str) -> PullOutcome:
        """Run git pull --ff-only in a worktree. Returns a PullOutcome."""
        try:
            out = self._run(["git", "pull", "--ff-only"], cwd=worktree_path)
            if "Already up to date" in out or "Already up-to-date" in out:
                return PullOutcome(status="up_to_date", new_commits=0)
            return PullOutcome(status="pulled", new_commits=0)
        except subprocess.CalledProcessError as e:
            raw_stderr = e.stderr or str(e)
            stderr = raw_stderr.lower()
            if "local changes" in stderr or "overwritten" in stderr:
                return PullOutcome(status="dirty", error=raw_stderr)
            return PullOutcome(status="non_ff", error=raw_stderr)

    def update_ref_from_remote(self, repo_path: str, branch: str) -> UpdateOutcome:
        """Fast-forward a local branch from origin without checking it out."""
        try:
            self._run(
                ["git", "fetch", "origin", f"{branch}:{branch}"], cwd=repo_path
            )
            return UpdateOutcome(status="pulled")
        except subprocess.CalledProcessError as e:
            raw_stderr = e.stderr or str(e)
            stderr = raw_stderr.lower()
            if "non-fast-forward" in stderr or "rejected" in stderr:
                return UpdateOutcome(status="non_ff", error=raw_stderr)
            return UpdateOutcome(status="error", error=raw_stderr)

    # ── diff methods ──────────────────────────────────────────────────────────

    def list_points(self, repo_path: str):
        from worktree_manager.diff_models import HistoryPoint
        points = [
            HistoryPoint(kind="working_tree_unstaged", label="Working tree (unstaged)"),
            HistoryPoint(kind="working_tree_staged",   label="Working tree (staged)"),
        ]
        branch_out = self._run(
            ["git", "log", "--branches", "--no-walk", "--format=%D\t%h\t%s"],
            cwd=repo_path,
        )
        for line in branch_out.splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 3:
                continue
            ref_names, sha, msg = parts
            for ref in ref_names.split(", "):
                ref = ref.strip()
                if ref.startswith("HEAD -> "):
                    ref = ref[len("HEAD -> "):]
                if ref and not ref.startswith("HEAD") and not ref.startswith("origin/") and not ref.startswith("tag:"):
                    points.append(HistoryPoint(kind="branch", label=ref, short_sha=sha, message=msg))
                    break
        commit_out = self._run(
            ["git", "log", "--format=%h\t%s", "-20"],
            cwd=repo_path,
        )
        for line in commit_out.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                sha, msg = parts
                points.append(HistoryPoint(kind="commit", label=sha, short_sha=sha, message=msg))
        return points

    def resolve_point(self, repo_path: str, point) -> str:
        from worktree_manager.diff_models import HistoryPoint
        if point.kind in ("working_tree_unstaged", "working_tree_staged"):
            return point.kind
        return point.short_sha or point.label

    def diff_files(self, repo_path: str, base_ref: str, target_ref: str):
        from worktree_manager.diff_models import DiffFile
        _WT_UNSTAGED = "working_tree_unstaged"
        _WT_STAGED   = "working_tree_staged"
        # Normalise: if base is a working-tree sentinel, swap so working-tree is always target
        if base_ref in (_WT_UNSTAGED, _WT_STAGED):
            base_ref, target_ref = target_ref, base_ref
        if target_ref == _WT_UNSTAGED:
            cmd = ["git", "diff", "--name-status", base_ref]
        elif target_ref == _WT_STAGED:
            cmd = ["git", "diff", "--name-status", "--cached", base_ref]
        else:
            cmd = ["git", "diff", "--name-status", base_ref, target_ref]
        out = self._run(cmd, cwd=repo_path)
        files = []
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            raw_status = parts[0]
            status_char = raw_status[0]
            if status_char == "R" and len(parts) >= 3:
                files.append(DiffFile(path=parts[2], status="R", old_path=parts[1]))
            elif len(parts) >= 2:
                files.append(DiffFile(path=parts[1], status=status_char))
        if target_ref == _WT_UNSTAGED:
            untracked_out = self._run(
                ["git", "ls-files", "--others", "--exclude-standard"], cwd=repo_path
            )
            for path in untracked_out.splitlines():
                if path.strip():
                    files.append(DiffFile(path=path.strip(), status="?"))
        return files

    def diff_hunks(self, repo_path: str, base_ref: str, target_ref: str, path: str):
        import re
        from worktree_manager.diff_models import DiffHunk
        _WT_UNSTAGED = "working_tree_unstaged"
        _WT_STAGED   = "working_tree_staged"
        if target_ref == _WT_UNSTAGED:
            cmd = ["git", "diff", base_ref, "--", path]
        elif target_ref == _WT_STAGED:
            cmd = ["git", "diff", "--cached", base_ref, "--", path]
        elif base_ref in (_WT_UNSTAGED, _WT_STAGED):
            # swap so working tree is always target
            return self.diff_hunks(repo_path, target_ref, base_ref, path)
        else:
            cmd = ["git", "diff", base_ref, target_ref, "--", path]
        out = self._run(cmd, cwd=repo_path)

        # Untracked files produce no diff output — synthesize an all-added hunk
        if not out.strip() and target_ref == _WT_UNSTAGED:
            abs_path = os.path.join(repo_path, path)
            try:
                with open(abs_path, encoding="utf-8", errors="replace") as fh:
                    content_lines = fh.read().splitlines()
            except OSError:
                return []
            if not content_lines:
                return []
            added = ["+" + ln for ln in content_lines]
            hunk = DiffHunk(
                index=0,
                header=f"@@ -0,0 +1,{len(content_lines)} @@",
                lines=added,
                old_start=0, old_count=0,
                new_start=1, new_count=len(content_lines),
            )
            return [hunk]
        hunks = []
        current_hunk = None
        hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)")
        for line in out.splitlines():
            m = hunk_re.match(line)
            if m:
                if current_hunk is not None:
                    hunks.append(current_hunk)
                old_start = int(m.group(1))
                old_count = int(m.group(2)) if m.group(2) is not None else 1
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) is not None else 1
                header = line
                current_hunk = DiffHunk(
                    index=len(hunks),
                    header=header,
                    lines=[],
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                )
            elif current_hunk is not None and not line.startswith(("diff ", "index ", "--- ", "+++ ")):
                current_hunk.lines.append(line)
        if current_hunk is not None:
            hunks.append(current_hunk)
        return hunks

    def apply_reverse_patch(self, repo_path: str, file_path: str, hunks: list) -> str:
        patch_text = self._build_patch(file_path, hunks)
        self._run_input(
            ["git", "apply", "--reverse"],
            cwd=repo_path,
            input=patch_text,
        )
        return patch_text

    def apply_patch(self, repo_path: str, patch_text: str) -> None:
        self._run_input(
            ["git", "apply"],
            cwd=repo_path,
            input=patch_text,
        )

    def checkout_file(self, repo_path: str, file_path: str, ref: str) -> None:
        self._run(["git", "checkout", ref, "--", file_path], cwd=repo_path)

    def infer_branch_suggestions(
        self, repo_path: str, current_branch: str
    ) -> tuple[str | None, str | None]:
        """Return (parent_branch, nearest_feature_or_main) by walking --first-parent log.

        Falls back to ("main", None) if nothing can be determined.
        """
        import subprocess

        # Collect the SHA each local branch points to so we can skip branches
        # that were merged INTO current_branch (their tip is an ancestor of HEAD)
        # rather than branches that current_branch was created FROM.
        try:
            merged_out = self._run(
                ["git", "branch", "--merged", "HEAD", "--format=%(refname:short)"],
                cwd=repo_path,
            )
            merged_branches: set[str] = set(merged_out.splitlines())
        except subprocess.CalledProcessError:
            merged_branches = set()

        try:
            out = self._run(
                ["git", "log", "--first-parent", "--simplify-by-decoration",
                 "--format=%D"],
                cwd=repo_path,
            )
        except subprocess.CalledProcessError:
            return ("main", None)

        parent: str | None = None
        feature_or_main: str | None = None

        for raw_line in out.splitlines():
            for token in raw_line.split(","):
                ref = token.strip()
                if ref.startswith("HEAD -> "):
                    ref = ref[len("HEAD -> "):]
                # Strip origin/ prefix so we can compare and use the local name
                is_origin = ref.startswith("origin/")
                local_ref = ref[len("origin/"):] if is_origin else ref
                if (not ref
                        or local_ref == "HEAD"
                        or ref.startswith("tag:")
                        or local_ref == current_branch
                        or local_ref in merged_branches):
                    continue
                # Prefer the plain local name; fall back to origin/X if no local exists
                candidate = local_ref if not is_origin else ref
                if parent is None:
                    parent = candidate
                if feature_or_main is None and (local_ref == "main" or local_ref.startswith("feature/")):
                    feature_or_main = candidate
                if parent is not None and feature_or_main is not None:
                    break
            if parent is not None and feature_or_main is not None:
                break

        if parent is None:
            # If already on main (or no parent found), no meaningful suggestion
            if current_branch == "main":
                return (None, None)
            return ("main", None)
        # deduplicate: if feature_or_main is same as parent, suppress it
        if feature_or_main == parent:
            feature_or_main = None
        return (parent, feature_or_main)

    def infer_parent_branch(
        self, repo_path: str, branch: str, candidates: list[str]
    ) -> str | None:
        """Return the inferred parent of branch if it appears in candidates, else None."""
        parent, _ = self.infer_branch_suggestions(repo_path, branch)
        return parent if parent and parent in candidates else None

    def rename_worktree(self, repo_path: str, old_path: str, new_path: str) -> None:
        if not os.path.exists(old_path):
            raise FileNotFoundError(f"Worktree path does not exist: {old_path}")
        if os.path.exists(new_path):
            raise FileExistsError(f"Target path already exists: {new_path}")
        os.rename(old_path, new_path)
        self._run(["git", "worktree", "repair"], cwd=new_path)

    def resolve_merge_base(self, repo_path: str, branch: str, onto: str) -> str:
        return self._run(["git", "merge-base", onto, branch], cwd=repo_path).strip()

    def commit_subject(self, repo_path: str, ref: str) -> str:
        return self._run(["git", "log", "-1", "--format=%s", ref], cwd=repo_path).strip()

    def _build_patch(self, file_path: str, hunks: list) -> str:
        lines = [
            f"--- a/{file_path}",
            f"+++ b/{file_path}",
        ]
        for hunk in hunks:
            lines.append(hunk.header)
            lines.extend(hunk.lines)
        return "\n".join(lines) + "\n"
