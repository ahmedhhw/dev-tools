import argparse
import json
import logging
import os
import socket
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)


def socket_path() -> Path:
    return Path.home() / ".config" / "worktree-manager" / "worktree-manager.sock"


def instance_is_running(sock_path: Path) -> bool:
    if not sock_path.exists():
        return False
    try:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.connect(str(sock_path))
        conn.close()
        return True
    except ConnectionRefusedError:
        sock_path.unlink()
        return False


def try_send_command(sock_path: Path, payload: dict) -> dict | None:
    try:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.connect(str(sock_path))
    except (FileNotFoundError, ConnectionRefusedError):
        return None
    f = conn.makefile("rw", encoding="utf-8")
    f.write(json.dumps(payload) + "\n")
    f.flush()
    line = f.readline()
    conn.close()
    return json.loads(line)
_pkg_root = str(Path(__file__).resolve().parent.parent)
if _pkg_root not in sys.path:
    sys.path.insert(0,_pkg_root)
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QMainWindow, QMessageBox,
    QWidget,
)

from worktree_manager.branch_mgmt_vm import BranchMgmtViewModel
from worktree_manager.github_vm import GitHubViewModel
from worktree_manager.command_center_vm import CommandCenterViewModel
from worktree_manager.config_store import ConfigStore
from worktree_manager.git_service import GitService
from worktree_manager.ipc_server import IpcServer
from worktree_manager.setup_settings_vm import RepoSetupViewModel, SettingsViewModel
from worktree_manager.workspace_projects_vm import WorkspaceProjectsViewModel
from worktree_manager.workspace_service import WorkspaceService
from worktree_manager.worktree_mgmt_vm import WorktreeMgmtViewModel
from worktree_manager.ui.cleanup_wizard import CleanupWizard
from worktree_manager.ui.command_center_panel import CommandCenterPanel
from worktree_manager.ui.create_dialog import CreateDialog
from worktree_manager.ui.repo_setup_dialog import RepoSetupDialog
from worktree_manager.ui.settings_panel import SettingsDialog
from worktree_manager.ui.launch_dialog import LaunchDialog
from worktree_manager.ui.workspace_projects_panel import WorkspaceProjectsPanel
from worktree_manager.ui.branch_management_panel import BranchManagementPanel
from worktree_manager.ui.worktree_management_panel import WorktreeManagementPanel


class _CleanupLoadBridge(QObject):
    progress_updated = Signal(int, int, str)
    loading_finished = Signal(list)


class _FinishedBridge(QObject):
    command_finished = Signal(str, object)
    startup_detected = Signal(str, object)


_SUBCOMMANDS = {"diff"}


def parse_args(argv):
    # If the first non-flag argument is a known subcommand, route to subparser.
    # Otherwise treat it as the legacy repo_path positional.
    first_positional = next((a for a in argv if not a.startswith("-")), None)
    if first_positional in _SUBCOMMANDS:
        # subcommand path
        parser = argparse.ArgumentParser(description="Git Worktree Manager")
        subparsers = parser.add_subparsers(dest="command")
        diff_parser = subparsers.add_parser("diff", help="Show diff panel for the current worktree")
        diff_parser.add_argument("--from", dest="from_ref", default=None,
                                 help="From ref for diff")
        diff_parser.add_argument("--to", dest="to_ref", default=None,
                                 help="To ref for diff")
        ns = parser.parse_args(argv)
        ns.repo_path = None
        return ns
    else:
        # bare-launch path (legacy)
        parser = argparse.ArgumentParser(description="Git Worktree Manager")
        parser.add_argument("repo_path", nargs="?", default=None,
                            help="Path to the main git worktree")
        ns = parser.parse_args(argv)
        ns.command = None
        return ns


def resolve_repo_path(path, git):
    if path is None:
        return None
    if not git.is_valid_repo(path):
        print(f"Error: '{path}' is not a git repository.", file=sys.stderr)
        sys.exit(1)
    return path


class App(QMainWindow):
    def __init__(self, repo_path=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Git Worktree Manager")
        self.setMinimumSize(1000, 500)

        self._store = ConfigStore()
        self._git = GitService()
        self._active_repo_path = None
        self._current_panel = None
        self._command_center_vm: CommandCenterViewModel | None = None
        self._panel_cache: dict[str, QWidget] = {}
        self._finished_bridge = _FinishedBridge()
        self._finished_bridge.command_finished.connect(self._on_command_finished)
        self._finished_bridge.startup_detected.connect(self._on_startup_detected)

        self._ipc_server = IpcServer()
        self._ipc_server.command_received.connect(self._handle_ipc_command)

        self._wt_mgmt_vm = WorktreeMgmtViewModel(
            config_store=self._store, git_service=self._git,
        )
        self._branch_mgmt_vm = BranchMgmtViewModel(
            config_store=self._store, git_service=self._git,
        )

        central = QWidget()
        self._central_layout = QHBoxLayout(central)
        self._central_layout.setContentsMargins(0, 0, 0, 0)
        self._central_layout.setSpacing(0)
        self.setCentralWidget(central)

        from worktree_manager.ui.sidebar import Sidebar
        self._sidebar_strip = None
        self._sidebar = Sidebar(
            store=self._store,
            on_command_center=self._show_command_center,
            on_workspace_projects=self._show_workspace_projects,
            on_branch_management=self._show_branch_management,
            on_worktree_management=self._show_worktree_management,
            on_diff=self._show_diff,
            on_github=self._show_github_panel,
            on_settings=self._handle_settings,
            on_refresh=self._refresh,
            on_hide=self._collapse_sidebar,
        )
        self._central_layout.addWidget(self._sidebar)

        if self._store.get_experimental_features():
            self._github_vm = GitHubViewModel(store=self._store)
            self._github_vm.pr_event.connect(self._on_pr_event)

        if repo_path:
            self._load_repo(repo_path)
        else:
            self._show_empty_main()

        self._setup_spotlight()

    def _setup_spotlight(self) -> None:
        from PySide6.QtGui import QKeySequence, QShortcut
        from worktree_manager.spotlight.action_parser import ActionParser
        from worktree_manager.spotlight.action_registry import (
            ActionRegistry, ActionSpec, ArgSlot,
        )
        from worktree_manager.ui.spotlight_overlay import SpotlightOverlay
        from worktree_manager.workspace_projects_vm import WorkspaceProjectsViewModel
        from worktree_manager.workspace_service import WorkspaceService

        self._spotlight_registry = ActionRegistry()
        self._wp_vm = WorkspaceProjectsViewModel(
            config_store=self._store,
            git_service=self._git,
            workspace_service=WorkspaceService(),
        )

        def _run_open_project(args):
            # Editor comes from the third token. Legacy nicknames saved before
            # the editor token existed have no "editor" arg — default to cursor.
            self._wp_vm.open_project(args["name"], args.get("editor", "cursor"))

        self._spotlight_registry.register(ActionSpec(
            name="open_project",
            keywords=["project"],
            slots=[
                ArgSlot(
                    name="name",
                    candidates=lambda prev: [p.name for p in self._store.all_projects()],
                ),
                ArgSlot(
                    name="editor",
                    candidates=lambda prev: ["cursor", "vscode"],
                ),
            ],
            runner=_run_open_project,
            description="Open a workspace project",
        ))

        from worktree_manager.ui.project_operations_dialog import (
            ProjectOperationsDialog,
        )

        def _run_edit_project(args):
            name = args["name"]
            project = self._store.get_project(name)
            if project is None:
                return
            dlg = ProjectOperationsDialog(
                parent=self, vm=self._wp_vm,
                repos=self._store.all_repos(),
                on_edit=lambda old, new, entries: self._wp_vm.update_project(
                    old_name=old, new_name=new, entries=entries,
                ),
                existing_project=project,
                config_store=self._store,
            )
            dlg.exec()

        self._spotlight_registry.register(ActionSpec(
            name="edit_project",
            keywords=["edit", "project"],
            slots=[ArgSlot(
                name="name",
                candidates=lambda prev: [p.name for p in self._store.all_projects()],
            )],
            runner=_run_edit_project,
            description="Edit a workspace project",
        ))

        def _repo_path_by_name(name: str) -> str | None:
            for path in self._store.all_repos():
                if Path(path).name == name:
                    return path
            return None

        def _run_focus_repo(args):
            path = _repo_path_by_name(args["name"])
            if path is not None:
                self._load_repo(path)

        self._spotlight_registry.register(ActionSpec(
            name="focus_repo",
            keywords=["repo"],
            slots=[ArgSlot(
                name="name",
                candidates=lambda prev: [Path(p).name for p in self._store.all_repos()],
            )],
            runner=_run_focus_repo,
            description="Focus a repo's main window",
        ))

        def _command_worktrees(prev):
            path = _repo_path_by_name(prev.get("repo", ""))
            if path is None:
                return []
            return [Path(w.path).name for w in self._git.list_worktrees(path)]

        def _command_cmd_names(prev):
            path = _repo_path_by_name(prev.get("repo", ""))
            if path is None:
                return []
            return [c.name for c in self._store.get_commands(path)]

        def _run_command(args):
            repo_path = _repo_path_by_name(args["repo"])
            if repo_path is None:
                return
            wt_path = None
            for w in self._git.list_worktrees(repo_path):
                if Path(w.path).name == args["worktree"]:
                    wt_path = w.path
                    break
            if wt_path is None:
                return
            cmd = next(
                (c for c in self._store.get_commands(repo_path) if c.name == args["cmd"]),
                None,
            )
            if cmd is None:
                return
            self._ensure_command_center_vm()
            self._command_center_vm.launch(
                repo_path=repo_path, repo_name=Path(repo_path).name,
                cmd_name=cmd.name, command_str=cmd.command,
                worktree_path=wt_path,
                startup_pattern=cmd.startup_pattern,
            )
            self._show_command_center()

        self._spotlight_registry.register(ActionSpec(
            name="run_command",
            keywords=["command"],
            slots=[
                ArgSlot(name="repo", candidates=lambda prev: [
                    Path(p).name for p in self._store.all_repos()
                ]),
                ArgSlot(name="worktree", candidates=_command_worktrees),
                ArgSlot(name="cmd", candidates=_command_cmd_names),
            ],
            runner=_run_command,
            description="Run a saved command",
        ))

        def _all_worktree_names(_prev):
            names = []
            for repo_path in self._store.all_repos():
                for w in self._git.list_worktrees(repo_path):
                    names.append(Path(w.path).name)
            return names

        def _branches_for_worktree(prev):
            wt_name = prev.get("worktree", "")
            for repo_path in self._store.all_repos():
                for w in self._git.list_worktrees(repo_path):
                    if Path(w.path).name == wt_name:
                        return self._git.list_local_branches(repo_path)
            return []

        def _run_switch(args):
            from worktree_manager.main_window_vm import MainWindowViewModel
            wt_name = args["worktree"]
            branch = args["branch"]
            for repo_path in self._store.all_repos():
                for w in self._git.list_worktrees(repo_path):
                    if Path(w.path).name == wt_name:
                        vm = MainWindowViewModel(
                            repo_path=repo_path,
                            config_store=self._store,
                            git_service=self._git,
                        )
                        vm.switch_branch(w.path, branch)
                        if self._current_panel is not None and hasattr(
                            self._current_panel, "refresh"
                        ):
                            self._current_panel.refresh()
                        return

        self._spotlight_registry.register(ActionSpec(
            name="switch_branch",
            keywords=["switch"],
            slots=[
                ArgSlot(name="worktree", candidates=_all_worktree_names),
                ArgSlot(name="branch", candidates=_branches_for_worktree),
            ],
            runner=_run_switch,
            description="Switch a worktree's branch",
        ))

        def _run_cleanup_repo(args):
            path = _repo_path_by_name(args["name"])
            if path is None:
                return
            from worktree_manager.main_window_vm import MainWindowViewModel
            vm = MainWindowViewModel(
                repo_path=path,
                config_store=self._store,
                git_service=self._git,
            )
            self._show_cleanup(vm)

        self._spotlight_registry.register(ActionSpec(
            name="cleanup_repo",
            keywords=["cleanup"],
            slots=[ArgSlot(
                name="name",
                candidates=lambda prev: [Path(p).name for p in self._store.all_repos()],
            )],
            runner=_run_cleanup_repo,
            description="Open cleanup wizard",
        ))

        def _run_settings(_args):
            self._handle_settings()

        self._spotlight_registry.register(ActionSpec(
            name="open_settings",
            keywords=["settings"],
            slots=[],
            runner=_run_settings,
            description="Open settings",
        ))

        def _run_nicknames(_args):
            from worktree_manager.ui.manage_nicknames_dialog import ManageNicknamesDialog
            dlg = ManageNicknamesDialog(parent=self, nickname_store=self._nickname_store)
            dlg.exec()

        self._spotlight_registry.register(ActionSpec(
            name="manage_nicknames",
            keywords=["nicknames"],
            slots=[],
            runner=_run_nicknames,
            description="View and delete saved nicknames",
        ))

        from worktree_manager.main_window_vm import MainWindowViewModel

        def _run_new_worktree(args):
            path = _repo_path_by_name(args["repo"])
            if path is None:
                return
            vm = MainWindowViewModel(
                repo_path=path,
                config_store=self._store,
                git_service=self._git,
            )
            self._show_new_worktree(vm)

        self._spotlight_registry.register(ActionSpec(
            name="new_worktree",
            keywords=["new", "worktree"],
            slots=[ArgSlot(
                name="repo",
                candidates=lambda prev: [Path(p).name for p in self._store.all_repos()],
            )],
            runner=_run_new_worktree,
            description="Create a new worktree",
        ))

        def _run_new_project(_args):
            dlg = ProjectOperationsDialog(
                parent=self, vm=self._wp_vm,
                repos=self._store.all_repos(),
                on_create=lambda name, entries: self._wp_vm.create_project(
                    name=name, entries=entries,
                ),
                config_store=self._store,
            )
            dlg.exec()

        self._spotlight_registry.register(ActionSpec(
            name="new_project",
            keywords=["new", "project"],
            slots=[],
            runner=_run_new_project,
            description="Create a new workspace project",
        ))

        from worktree_manager.ui.add_command_dialog import AddCommandDialog

        def _run_new_command(args):
            path = _repo_path_by_name(args["repo"])
            if path is None:
                return
            self._ensure_command_center_vm()
            dlg = AddCommandDialog(
                parent=self, vm=self._command_center_vm,
                initial_repo=path,
            )
            dlg.exec()

        self._spotlight_registry.register(ActionSpec(
            name="new_command",
            keywords=["new", "command"],
            slots=[ArgSlot(
                name="repo",
                candidates=lambda prev: [Path(p).name for p in self._store.all_repos()],
            )],
            runner=_run_new_command,
            description="Add a new saved command",
        ))

        def _run_edit_command(args):
            from worktree_manager.ui.launch_dialog import LaunchDialog
            path = _repo_path_by_name(args["repo"])
            if path is None:
                return
            self._ensure_command_center_vm()
            dlg = LaunchDialog(
                parent=self, vm=self._command_center_vm,
                locked_repo_path=path,
            )
            dlg.exec()

        self._spotlight_registry.register(ActionSpec(
            name="edit_command",
            keywords=["edit", "command"],
            slots=[ArgSlot(
                name="repo",
                candidates=lambda prev: [Path(p).name for p in self._store.all_repos()],
            )],
            runner=_run_edit_command,
            description="Manage saved commands for a repo",
        ))

        self._spotlight_registry.register(ActionSpec(
            name="new_repo",
            keywords=["new", "repo"],
            slots=[],
            runner=lambda _args: self._pick_and_add_repo(),
            description="Add a new repo",
        ))

        from worktree_manager.ui.spotlight_confirm_dialog import SpotlightConfirmDialog

        def _delete_worktree_worktrees(prev):
            path = _repo_path_by_name(prev.get("repo", ""))
            if path is None:
                return []
            return [Path(w.path).name for w in self._git.list_worktrees(path)]

        def _run_delete_worktree(args):
            repo_path = _repo_path_by_name(args["repo"])
            if repo_path is None:
                return
            wt_name = args["worktree"]
            wt = next(
                (w for w in self._git.list_worktrees(repo_path)
                 if Path(w.path).name == wt_name),
                None,
            )
            if wt is None:
                return
            is_protected = False
            try:
                from worktree_manager.main_window_vm import MainWindowViewModel
                vm = MainWindowViewModel(
                    repo_path=repo_path,
                    config_store=self._store,
                    git_service=self._git,
                )
                is_protected = vm.is_protected_branch(wt.branch)
            except Exception:
                pass
            dlg = SpotlightConfirmDialog(
                parent=self,
                title="Delete Worktree",
                message=f'Delete worktree "{wt_name}"?\n\nBranch: {wt.branch}\nPath: {wt.path}',
                show_also_branch=True,
                branch_protected=is_protected,
            )
            if dlg.exec() != SpotlightConfirmDialog.Accepted:
                return
            from worktree_manager.main_window_vm import MainWindowViewModel
            vm = MainWindowViewModel(
                repo_path=repo_path,
                config_store=self._store,
                git_service=self._git,
            )
            vm.delete_worktree(
                path=wt.path, branch=wt.branch,
                also_delete_branch=dlg.also_delete_branch(),
            )
            if self._current_panel is not None and hasattr(self._current_panel, "refresh"):
                self._current_panel.refresh()

        self._spotlight_registry.register(ActionSpec(
            name="delete_worktree",
            keywords=["delete", "worktree"],
            slots=[
                ArgSlot(name="repo", candidates=lambda prev: [
                    Path(p).name for p in self._store.all_repos()
                ]),
                ArgSlot(name="worktree", candidates=_delete_worktree_worktrees),
            ],
            runner=_run_delete_worktree,
            description="Delete a worktree",
        ))

        def _run_delete_project(args):
            name = args["name"]
            dlg = SpotlightConfirmDialog(
                parent=self,
                title="Delete Project",
                message=f'Delete workspace project "{name}"?',
            )
            if dlg.exec() != SpotlightConfirmDialog.Accepted:
                return
            self._wp_vm.delete_project(name)

        self._spotlight_registry.register(ActionSpec(
            name="delete_project",
            keywords=["delete", "project"],
            slots=[ArgSlot(
                name="name",
                candidates=lambda prev: [p.name for p in self._store.all_projects()],
            )],
            runner=_run_delete_project,
            description="Delete a workspace project",
        ))

        def _run_delete_command(args):
            repo_path = _repo_path_by_name(args["repo"])
            if repo_path is None:
                return
            cmd_name = args["cmd"]
            dlg = SpotlightConfirmDialog(
                parent=self,
                title="Delete Command",
                message=f'Delete saved command "{cmd_name}" from {args["repo"]}?',
            )
            if dlg.exec() != SpotlightConfirmDialog.Accepted:
                return
            self._store.delete_command(repo_path, cmd_name)

        self._spotlight_registry.register(ActionSpec(
            name="delete_command",
            keywords=["delete", "command"],
            slots=[
                ArgSlot(name="repo", candidates=lambda prev: [
                    Path(p).name for p in self._store.all_repos()
                ]),
                ArgSlot(name="cmd", candidates=_command_cmd_names),
            ],
            runner=_run_delete_command,
            description="Delete a saved command",
        ))

        def _run_delete_repo(args):
            path = _repo_path_by_name(args["name"])
            if path is None:
                return
            self._confirm_delete_repo(path, is_active=(path == self._active_repo_path))

        self._spotlight_registry.register(ActionSpec(
            name="delete_repo",
            keywords=["delete", "repo"],
            slots=[ArgSlot(
                name="name",
                candidates=lambda prev: [Path(p).name for p in self._store.all_repos()],
            )],
            runner=_run_delete_repo,
            description="Remove a repo from the app",
        ))

        from worktree_manager.spotlight.nickname_store import NicknameStore

        self._nickname_store = NicknameStore(self._store)

        def _build_mru_labels() -> list[str]:
            labels = []
            for entry in self._store.get_mru():
                action_name = entry.get("action", "")
                args = entry.get("args", {})
                spec = self._spotlight_registry.get_by_name(action_name)
                if spec is None:
                    continue
                parts = list(spec.keywords) + [args[s.name] for s in spec.slots if s.name in args]
                labels.append(" ".join(parts))
            return labels

        def _on_action_executed(action_name: str, args: dict) -> None:
            self._store.push_mru(action_name, args)

        self._build_mru_labels = _build_mru_labels

        self._spotlight_overlay = SpotlightOverlay(
            parser=ActionParser(
                self._spotlight_registry,
                nickname_store=self._nickname_store,
                mru_labels=_build_mru_labels(),
            ),
            parent=None,
            on_action_executed=_on_action_executed,
        )
        from PySide6.QtCore import Qt as _Qt
        seq = self._store.get_spotlight_shortcut()
        self._spotlight_qshortcut = QShortcut(QKeySequence(seq), self)
        self._spotlight_qshortcut.setContext(_Qt.ApplicationShortcut)
        self._spotlight_qshortcut.activated.connect(self._open_spotlight)

        from worktree_manager.global_hotkey import GlobalHotkey
        from PySide6.QtCore import Qt as _Qt2
        self._global_hotkey = GlobalHotkey(parent=self)
        self._global_hotkey.triggered.connect(
            self._open_spotlight, _Qt2.ConnectionType.QueuedConnection
        )
        self.apply_spotlight_shortcut(seq)

    def apply_spotlight_shortcut(self, seq: str) -> bool:
        """Rebind the in-process QShortcut and re-register the global Carbon hotkey.

        Returns True if the global hotkey was registered successfully (or on
        non-macOS where the in-process shortcut is the only path).  Returns
        False if Carbon registration failed; the in-process shortcut still works.
        """
        from PySide6.QtGui import QKeySequence as _QKS
        from worktree_manager.spotlight.combo import parse_combo
        self._spotlight_qshortcut.setKey(_QKS(seq))
        try:
            keycode, mask = parse_combo(seq)
        except ValueError:
            return False
        return self._global_hotkey.register(keycode, mask)

    def _open_spotlight(self) -> None:
        from worktree_manager.spotlight.action_parser import ActionParser
        from worktree_manager.spotlight.nickname_store import NicknameStore
        self._spotlight_overlay._parser = ActionParser(
            self._spotlight_registry,
            nickname_store=self._nickname_store,
            mru_labels=self._build_mru_labels(),
        )
        self._spotlight_overlay.show_centered_over(self)

    def spotlight_registry(self):
        return self._spotlight_registry

    def open_spotlight_for_test(self):
        self._open_spotlight()
        return self._spotlight_overlay

    def _add_nickname(self, action_name: str, args: dict) -> None:
        from worktree_manager.ui.add_nickname_dialog import AddNicknameDialog
        from worktree_manager.spotlight.nickname_store import NicknameEntry
        reserved = list(self._spotlight_registry.root_keywords())
        existing = list(self._nickname_store.all().keys())
        dlg = AddNicknameDialog(parent=self, reserved_keywords=reserved, existing_nicknames=existing)
        if dlg.exec():
            nickname = dlg.nickname()
            self._nickname_store.save(NicknameEntry(
                nickname=nickname,
                action_name=action_name,
                args=dict(args),
            ))

    # ── sidebar collapse ────────────────────────────────────────────────────────

    def _collapse_sidebar(self) -> None:
        from worktree_manager.ui.sidebar_strip import SidebarStrip
        self._sidebar.hide()
        self._sidebar_strip = SidebarStrip(on_restore=self._restore_sidebar)
        self._central_layout.insertWidget(0, self._sidebar_strip)

    def _restore_sidebar(self) -> None:
        if self._sidebar_strip is not None:
            self._central_layout.removeWidget(self._sidebar_strip)
            self._sidebar_strip.deleteLater()
            self._sidebar_strip = None
        self._sidebar.show()

    # ── panel swap helpers ──────────────────────────────────────────────────────

    def _set_panel(self, widget):
        if self._current_panel is not None:
            self._central_layout.removeWidget(self._current_panel)
            if self._current_panel in self._panel_cache.values():
                self._current_panel.hide()
            else:
                self._current_panel.deleteLater()
        self._current_panel = widget
        self._central_layout.addWidget(widget, 1)

    def _show_empty_main(self):
        from worktree_manager.ui.landing_screen import LandingScreen
        self._set_panel(LandingScreen())

    # ── repo lifecycle ──────────────────────────────────────────────────────────

    def _pick_and_add_repo(self):
        path = QFileDialog.getExistingDirectory(self, "Select git repo")
        if not path:
            return
        if not self._git.is_valid_repo(path):
            QMessageBox.critical(self, "Error", f"'{path}' is not a git repository.")
            return
        if self._store.get_repo(path) is not None:
            QMessageBox.information(self, "Already added", f"'{Path(path).name}' is already in your repos.")
            return
        self._load_repo(path)

    def _load_repo(self, repo_path):
        cfg = self._store.get_repo(repo_path)
        if cfg is None:
            vm = RepoSetupViewModel(repo_path=repo_path, config_store=self._store)
            dlg = RepoSetupDialog(
                parent=self, vm=vm,
                on_confirm=lambda: self._show_main(repo_path),
            )
            dlg.exec()
            return
        self._show_main(repo_path)

    def _show_main(self, repo_path):
        self._active_repo_path = repo_path
        self._wt_mgmt_vm.select_repo(repo_path)
        self._show_worktree_management()

    def _confirm_delete_repo(self, repo_path, is_active):
        name = Path(repo_path).name
        extra = (
            "\n\nThis is the currently open repo. Removing it will return you to the empty screen."
            if is_active else ""
        )
        ans = QMessageBox.question(
            self, "Remove repo",
            f'Remove "{name}" from the app?\n\n{repo_path}{extra}\n\nFiles on disk are not affected.',
        )
        if ans != QMessageBox.Yes:
            return
        self._store.delete_repo(repo_path)
        if is_active:
            self._active_repo_path = None
            self._wt_mgmt_vm.select_repo(None)
            self._show_empty_main()
        elif isinstance(self._current_panel, WorktreeManagementPanel):
            self._current_panel.populate_repos()

    def _refresh(self):
        # Refresh the visible panel first so the user sees an immediate response,
        # then refresh all other cached panels so their data is current on next visit.
        if self._current_panel is not None and hasattr(self._current_panel, "refresh"):
            self._current_panel.refresh()
        for panel in self._panel_cache.values():
            if panel is not self._current_panel and hasattr(panel, "refresh"):
                panel.refresh()

    # ── tab panel handlers ──────────────────────────────────────────────────────

    def _show_worktree_management(self):
        if "worktree_management" not in self._panel_cache:
            self._panel_cache["worktree_management"] = WorktreeManagementPanel(
                vm=self._wt_mgmt_vm,
                on_add_repo=self._pick_and_add_repo,
                on_refresh=self._refresh,
                on_cleanup=self._show_cleanup_for_repo,
                on_new_worktree=self._show_new_worktree,
                on_generate_project=self._on_generate_project,
                on_run_command=self._on_run_command,
                on_nickname=lambda action_name, args: self._add_nickname(action_name, args),
                on_diff_from_working_tree=self._diff_from_working_tree,
                on_diff_compare_branches=self._diff_compare_branches,
            )
        panel = self._panel_cache["worktree_management"]
        self._set_panel(panel)
        panel.show()

    def _show_branch_management(self):
        first_visit = "branch_management" not in self._panel_cache
        if first_visit:
            self._panel_cache["branch_management"] = BranchManagementPanel(
                vm=self._branch_mgmt_vm,
            )
        panel = self._panel_cache["branch_management"]
        self._set_panel(panel)
        panel.show()
        if first_visit:
            panel.show_sync()

    def _show_github_panel(self):
        if not hasattr(self, "_github_vm"):
            from worktree_manager.ui.github_panel import GitHubPanel
            self._github_vm = GitHubViewModel(store=self._store)
            self._github_vm.pr_event.connect(self._on_pr_event)
        if "github" not in self._panel_cache:
            from worktree_manager.ui.github_panel import GitHubPanel
            self._panel_cache["github"] = GitHubPanel(vm=self._github_vm)
        panel = self._panel_cache["github"]
        self._set_panel(panel)
        self._sidebar.set_active_tab("github")
        panel.show()

    def _show_diff(self):
        if "diff" not in self._panel_cache:
            from worktree_manager.ui.diff_panel import DiffPanel
            self._panel_cache["diff"] = DiffPanel(
                git_service=self._git,
                config_store=self._store,
            )
        panel = self._panel_cache["diff"]
        self._set_panel(panel)
        self._sidebar.set_active_tab("diff")
        panel.show()

    def _diff_from_working_tree(self, worktree_path: str) -> None:
        repo_path = self._repo_path_for_worktree(worktree_path)
        self._show_diff()
        panel = self._panel_cache["diff"]
        panel.show_diff(repo_path, worktree_path=worktree_path,
                        to_ref="working_tree_unstaged", from_ref=None)

    def _diff_compare_branches(self, repo_path: str) -> None:
        self._show_diff()
        panel = self._panel_cache["diff"]
        panel.show_for_repo(repo_path)

    # ── IPC / single-instance ────────────────────────────────────────────────

    def raise_and_activate(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _handle_ipc_command(self, request: dict, conn) -> None:
        action = request.get("action")
        if action == "focus":
            self.raise_and_activate()
            reply = {"ok": True}
        elif action == "diff":
            reply = self._handle_diff_command(request)
        else:
            reply = {"ok": False, "error": f"unknown action {action!r}"}
        try:
            f = conn.makefile("w", encoding="utf-8")
            f.write(json.dumps(reply) + "\n")
            f.flush()
        except Exception:
            pass
        finally:
            conn.close()

    def _handle_diff_command(self, request: dict) -> dict:
        cwd = request.get("cwd", "")
        from_ref = request.get("from_ref")
        to_ref = request.get("to_ref")
        toplevel = self._git.toplevel_for(cwd)
        if toplevel is None:
            return {"ok": False, "error": "not inside a git repository"}
        repo_path = self._find_tracked_repo_for_toplevel(toplevel)
        if repo_path is None:
            return {"ok": False, "error": "cwd is not part of any tracked repo"}
        worktrees = self._git.list_worktrees(repo_path)
        worktree = next((w for w in worktrees if w.path == toplevel), None)
        if worktree is None:
            return {"ok": False, "error": "cwd's worktree is not tracked"}
        for ref in (from_ref, to_ref):
            if ref and not self._git.ref_exists(repo_path, ref):
                return {"ok": False, "error": f"cannot resolve ref {ref!r}"}
        self.raise_and_activate()
        self._show_diff()
        if from_ref or to_ref:
            self._panel_cache["diff"].show_diff(
                repo_path, worktree_path=worktree.path,
                from_ref=from_ref, to_ref=to_ref,
            )
        else:
            self._diff_from_working_tree(worktree.path)
        return {"ok": True}

    def _find_tracked_repo_for_toplevel(self, toplevel: str) -> str | None:
        for repo in self._store.all_repos():
            for wt in self._git.list_worktrees(repo):
                if wt.path == toplevel:
                    return repo
        return None

    def _handle_settings(self):
        repo_path = self._active_repo_path or next(iter(self._store.all_repos()), None)
        if repo_path is None:
            return
        self._show_settings(repo_path)

    # ── stubs for panels arriving in later iterations ───────────────────────

    def _show_settings(self, repo_path):
        vm = SettingsViewModel(repo_path=repo_path, config_store=self._store)
        dlg = SettingsDialog(parent=self, vm=vm, store=self._store)
        dlg.exec()
        self._refresh()

    def _show_cleanup(self, vm):
        def _on_delete(selected: list):
            vm.delete_cleanup_candidates(selected, also_delete_branches=True)
            if self._current_panel is not None and hasattr(self._current_panel, "refresh"):
                self._current_panel.refresh()

        wizard = CleanupWizard(
            parent=self, candidates=None, on_delete_selected=_on_delete,
        )

        bridge = _CleanupLoadBridge()
        bridge.progress_updated.connect(wizard.update_progress)
        bridge.loading_finished.connect(
            lambda candidates: self._on_cleanup_loaded(wizard, candidates)
        )

        def _load():
            def _on_progress(current, total, label):
                bridge.progress_updated.emit(current, total, label)
            candidates = vm.all_cleanup_candidates(on_progress=_on_progress)
            bridge.loading_finished.emit(candidates)

        threading.Thread(target=_load, daemon=True).start()
        wizard.exec()

    def _show_cleanup_for_repo(self, repo_path: str):
        if not isinstance(self._current_panel, BranchManagementPanel):
            self._show_branch_management()
        self._current_panel.show_cleanup(repo_path=repo_path)
        self._sidebar.set_active_tab("branch_management")

    def _on_cleanup_loaded(self, wizard, candidates: list) -> None:
        if not candidates:
            wizard.reject()
            QMessageBox.information(self, "Cleanup", "No branches to clean up.")
            return
        wizard.finish_loading(candidates)

    def _show_new_worktree(self, vm):
        vm.load_worktrees()
        all_branches = vm.list_local_branches()
        available = vm.list_available_branches()

        def _on_create(branch, base_branch, is_existing, worktree_name):
            try:
                vm.create_worktree(
                    branch=branch, base_branch=base_branch,
                    existing=is_existing, worktree_name=worktree_name,
                )
            except ValueError as e:
                QMessageBox.critical(self, "Cannot create worktree", str(e))
                return
            if self._current_panel is not None and hasattr(self._current_panel, "refresh"):
                self._current_panel.refresh()

        dlg = CreateDialog(
            parent=self, branches=all_branches,
            existing_branches=available, on_create=_on_create,
        )
        dlg.exec()

    def _ensure_command_center_vm(self) -> CommandCenterViewModel:
        if self._command_center_vm is None:
            self._command_center_vm = CommandCenterViewModel(
                config_store=self._store, git_service=self._git,
            )
            self._command_center_vm.on_finished = (
                self._finished_bridge.command_finished.emit
            )
            self._command_center_vm.on_startup_detected = (
                self._finished_bridge.startup_detected.emit
            )
        return self._command_center_vm

    def _show_command_center(self):
        self._ensure_command_center_vm()
        if "command_center" not in self._panel_cache:
            self._panel_cache["command_center"] = CommandCenterPanel(
                parent=self,
                vm=self._command_center_vm,
                on_close=self._on_command_center_close,
                on_nickname=lambda action_name, args: self._add_nickname(action_name, args),
            )
        panel = self._panel_cache["command_center"]
        self._set_panel(panel)
        panel.show()

    def _on_command_center_close(self):
        self._show_empty_main()

    def _on_workspace_projects_close(self):
        self._show_empty_main()

    def _show_workspace_projects(self):
        if "workspace_projects" not in self._panel_cache:
            vm = WorkspaceProjectsViewModel(
                config_store=self._store,
                git_service=self._git,
                workspace_service=WorkspaceService(),
            )
            self._panel_cache["workspace_projects"] = WorkspaceProjectsPanel(
                parent=self, vm=vm, on_close=self._on_workspace_projects_close,
                on_generate_project=self._on_generate_project,
                on_run_command=self._on_run_command,
                on_nickname=lambda action_name, args: self._add_nickname(action_name, args),
                on_diff_from_working_tree=self._diff_from_working_tree,
                on_diff_compare_branches=self._diff_compare_branches,
            )
        panel = self._panel_cache["workspace_projects"]
        self._set_panel(panel)
        panel.show()

    def _repo_path_for_worktree(self, worktree_path: str) -> str:
        for repo in self._store.all_repos():
            for wt in self._git.list_worktrees(repo):
                if wt.path == worktree_path:
                    return repo
        return self._active_repo_path or worktree_path

    def _on_run_command(self, worktree_path: str) -> None:
        self._ensure_command_center_vm()
        repo_path = self._repo_path_for_worktree(worktree_path)
        run_count_before = len(self._command_center_vm.all_runs())

        dlg = LaunchDialog(
            parent=self,
            vm=self._command_center_vm,
            locked_repo_path=repo_path,
            locked_worktree_path=worktree_path,
        )
        dlg.exec()

        if len(self._command_center_vm.all_runs()) > run_count_before:
            self._show_command_center()

    def _notifications_enabled(self) -> bool:
        return bool(self._store.get_ui_pref(
            "cmd_center_notifications_enabled", True
        ))

    def _on_pr_event(self, pr_key: tuple, event_type: str, message: str) -> None:
        if self._store.get_ui_pref("github_notifications_enabled", True):
            self._show_notification("Pull Requests", message)
            if not self.isActiveWindow():
                QApplication.alert(self, 0)

    def _on_command_finished(self, run_id: str, handle) -> None:
        from worktree_manager.command_runner import RunStatus

        cmd_name = handle.cmd_name
        if handle.status == RunStatus.ERROR:
            body = f"❌ \"{cmd_name}\" exited with code {handle.returncode}"
        elif handle.returncode == 0:
            body = f"✅ \"{cmd_name}\" finished"
        else:
            body = f"⏹ \"{cmd_name}\" stopped"
        if self._notifications_enabled():
            self._show_notification("Command Center", body)
            if not self.isActiveWindow():
                QApplication.alert(self, 0)
        self._show_command_center()

    def _on_startup_detected(self, run_id: str, handle) -> None:
        cmd_name = handle.cmd_name
        if self._notifications_enabled() and not self.isActiveWindow():
            self._show_notification("Command Center", f"🚀 \"{cmd_name}\" is ready")
            QApplication.alert(self, 0)
        self.show_toast(f"🚀 \"{cmd_name}\" is ready")
        if not isinstance(self._current_panel, CommandCenterPanel):
            self._show_command_center()

    def show_toast(self, message: str) -> None:
        self.statusBar().showMessage(message, 4000)

    def _show_notification(self, title: str, body: str) -> None:
        import subprocess
        safe_title = title.replace('"', '\\"')
        safe_body = body.replace('"', '\\"')
        subprocess.Popen(
            ["osascript", "-e",
             f'display notification "{safe_body}" with title "{safe_title}"'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _on_generate_project(self, worktree_path: str) -> None:
        from worktree_manager.models import WorkspaceEntry, WorkspaceProject
        name = os.path.basename(worktree_path) or worktree_path
        project = WorkspaceProject(
            name=name,
            entries=[WorkspaceEntry(worktree_path=worktree_path)],
        )
        svc = WorkspaceService()
        svc.generate_code_workspace(project)
        action = "updated" if self._store.get_project(name) else "created"
        self._store.save_project(project)
        self.show_toast(f"✅ Project \"{name}\" {action}")


def force_light_mode(app):
    """Force the QApplication to render in light mode regardless of OS setting."""
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)


def main():
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = parse_args(sys.argv[1:])
    sock_path = socket_path()

    if args.command == "diff":
        reply = try_send_command(sock_path, {
            "action": "diff",
            "cwd": os.getcwd(),
            "from_ref": args.from_ref,
            "to_ref": args.to_ref,
        })
        if reply is None:
            print("No running instance. Launch worktree-manager first.", file=sys.stderr)
            sys.exit(1)
        if not reply.get("ok"):
            print(f"Error: {reply.get('error')}", file=sys.stderr)
            sys.exit(1)
        return

    # bare launch — single-instance check before QApplication
    if instance_is_running(sock_path):
        try_send_command(sock_path, {"action": "focus"})
        return

    git = GitService()
    repo_path = resolve_repo_path(args.repo_path, git)

    qt_app = QApplication.instance() or QApplication(sys.argv)
    force_light_mode(qt_app)
    window = App(repo_path=repo_path)
    window._ipc_server.start(str(sock_path))
    window.show()
    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()
