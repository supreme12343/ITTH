"""Launch the MAIC classroom with Campus Suite's pinned package manager."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
import atexit
import json
import hashlib
import socket
import tempfile
import ipaddress
import re
import shlex
from contextlib import contextmanager
from pathlib import Path

from unified_memory import record_activity

ROOT = Path(__file__).resolve().parent
MAIC_DIR = ROOT / "MAIC"
PORT = os.environ.get("MAIC_PORT", "3100")
AGENT_PORT = os.environ.get("MAIC_AGENT_PORT", "3101")
CAMPUS_AI_PORT = os.environ.get("CAMPUS_AI_PORT", "3110")
LOCAL_BIND_HOST = "127.0.0.1"
_gateway_process: subprocess.Popen | None = None


def inherit_provider_settings(env: dict[str, str]) -> None:
    """Load Campus Suite's provider settings into the MAIC server process.

    MAIC is a separate Node process, so it cannot see the root .env unless the
    desktop bridge forwards it. Keep the forwarding server-side and include
    the key-manager/OmniRoute names as well as the normal provider names.
    """
    dotenv = ROOT / ".env"
    try:
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.endswith(("_API_KEY", "_BASE_URL", "_MODELS")) and not env.get(key):
                env[key] = value.strip().strip('"').strip("'")
    except OSError:
        pass

    # MAIC's OpenAI-compatible provider points to the local AiApi adapter. The
    # adapter itself calls Campus Suite's Python key manager.


_MAIC_ROUTER_STAGES = (
    "scene-outlines-stream",
    "scene-content",
    "scene-actions",
    "agent-profiles",
    "quiz-grade",
    "pbl-chat",
    "pbl-v2-runtime",
    "pbl-v2-runtime:instructor",
    "pbl-v2-runtime:open-task",
    "pbl-v2-runtime:evaluate",
    "pbl-v2-runtime:simulator",
    "chat-adapter",
    "generate-classroom",
    "web-search-query-rewrite",
    "maic-agent",
)


def configure_maic_provider_router(env: dict[str, str]) -> None:
    """Route MAIC's model stages through Campus Suite's shared provider router.

    The local OpenAI-compatible URL is only the transport into Campus Suite's
    Python key manager. Provider credentials stay optional: the shared router
    skips providers that are not configured and applies its own fallback order.
    """
    env["ENABLE_CODEX_PROVIDER"] = "true"
    env["CODEX_ONLY_PROVIDER"] = "false"
    env["ENABLE_OMNIROUTE"] = "true"
    env.setdefault("CAMPUS_GEMINI_MODEL", "gemini-3.1-flash-lite")
    env["CAMPUS_AI_PORT"] = CAMPUS_AI_PORT
    env["OPENAI_API_KEY"] = env.get("CAMPUS_SUITE_AI_TOKEN", "campus-suite-local")
    env["OPENAI_BASE_URL"] = f"http://127.0.0.1:{CAMPUS_AI_PORT}/v1"
    env["DEFAULT_MODEL"] = "openai:gpt-6-luna"
    # Do not let AI SDK fallback models bypass the shared Campus Suite router.
    env.pop("MODEL_FALLBACK", None)
    env["MODEL_ROUTES"] = json.dumps(
        {stage: "openai:gpt-6-luna" for stage in _MAIC_ROUTER_STAGES}
    )
    env["CAMPUS_AI_GATEWAY_URL"] = f"http://127.0.0.1:{CAMPUS_AI_PORT}/v1/chat/completions"


def corepack_pnpm(*args: str) -> list[str]:
    """Build a Windows-safe Corepack command (corepack is a .cmd shim here)."""
    return ["cmd", "/c", "corepack", "pnpm", *args] if os.name == "nt" else ["corepack", "pnpm", *args]


def _next_server_command(
    node_binary: str,
    next_cli: Path,
    command: str,
    standalone_server: Path,
) -> list[str]:
    """Construct a desktop Next.js command with an explicit local dev bind."""
    if command == "start":
        # The generated standalone entry point reads HOSTNAME from its env.
        return [node_binary, str(standalone_server)]
    # `next dev` defaults to all interfaces. Its documented hostname option
    # keeps this desktop-only server on loopback regardless of inherited env.
    return [node_binary, str(next_cli), command, "--webpack", "--hostname", LOCAL_BIND_HOST]


def _configure_next_server_environment(env: dict[str, str]) -> None:
    """Set the bind address read by generated Next standalone servers."""
    env["HOSTNAME"] = LOCAL_BIND_HOST


def _server_is_ready(url: str, timeout: float = 2, expected_text: str | None = None) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return False
            if expected_text is None:
                return True
            if "text/html" not in response.headers.get("Content-Type", ""):
                return False
            return expected_text in response.read(512_000).decode("utf-8", errors="replace")
    except OSError:
        return False


def _agent_workspace_build_fingerprint(site_dir: Path) -> str:
    """Fingerprint Agent Workspace sources and public assets for build reuse."""
    public_dir = site_dir / "public"
    source_paths = [
        site_dir / "app",
        public_dir,
        site_dir / "next.config.ts",
        site_dir / "package.json",
        site_dir / "tsconfig.json",
        site_dir / "postcss.config.mjs",
        MAIC_DIR / "components" / "campus-agent-workspace.tsx",
        MAIC_DIR / "app" / "api" / "campus-agents",
        MAIC_DIR / "lib" / "server" / "api-response.ts",
        MAIC_DIR / "lib" / "utils" / "index.ts",
        MAIC_DIR / "lib" / "utils" / "cn.ts",
        MAIC_DIR / "lib" / "agent-workspace" / "conversations.ts",
        MAIC_DIR / "lib" / "agent-workspace" / "history.ts",
        MAIC_DIR / "package.json",
        MAIC_DIR / "tsconfig.json",
    ]
    digest = hashlib.sha256()
    text_extensions = {".css", ".js", ".jsx", ".json", ".mjs", ".ts", ".tsx"}
    files: set[Path] = set()
    for source in source_paths:
        if source.is_dir():
            source_files = {
                path for path in source.rglob("*")
                if path.is_file()
            }
            if source == public_dir:
                # Public files include logos, images, fonts, and media. Their
                # contents must participate in the fingerprint so a changed
                # asset cannot reuse stale standalone output.
                files.update(source_files)
            else:
                files.update(path for path in source_files if path.suffix.lower() in text_extensions)
        elif source.is_file():
            files.add(source)
    for path in sorted(files):
        digest.update(path.relative_to(MAIC_DIR).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())

    # The lockfile participates in dependency resolution, so hash its contents
    # rather than relying on metadata that can be preserved across edits.
    lockfile = MAIC_DIR / "pnpm-lock.yaml"
    if lockfile.is_file():
        digest.update(b"pnpm-lock.yaml\0")
        digest.update(lockfile.read_bytes())
    return digest.hexdigest()


def _standalone_server_path(site_dir: Path) -> Path:
    """Locate standalone output when Next traces from the MAIC monorepo root."""
    app_dir = site_dir.relative_to(MAIC_DIR)
    return site_dir / ".next" / "standalone" / app_dir / "server.js"


def _standalone_asset_entries(site_dir: Path) -> list[tuple[str, Path, Path]]:
    standalone_dir = _standalone_server_path(site_dir).parent
    entries: list[tuple[str, Path, Path]] = []
    for label, source_dir, target_dir in (
        ("public", site_dir / "public", standalone_dir / "public"),
        ("static", site_dir / ".next" / "static", standalone_dir / ".next" / "static"),
    ):
        if not source_dir.is_dir():
            continue
        for source in sorted(source_dir.rglob("*")):
            if source.is_file():
                entries.append((label, source, target_dir / source.relative_to(source_dir)))
    return entries


def _path_is_within(path: Path, parent: Path, *, allow_equal: bool = False) -> bool:
    """Return whether a resolved path is inside its resolved parent directory."""
    try:
        candidate = os.path.normcase(str(path.resolve()))
        root = os.path.normcase(str(parent.resolve()))
        return os.path.commonpath((candidate, root)) == root and (allow_equal or candidate != root)
    except (OSError, ValueError):
        return False


def _standalone_assets_fingerprint(entries: list[tuple[str, Path, Path]], site_dir: Path) -> str:
    digest = hashlib.sha256()
    for label, source, _target in entries:
        relative = source.relative_to(site_dir).as_posix()
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with source.open("rb") as asset:
            while chunk := asset.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _prepare_standalone_assets(site_dir: Path) -> bool:
    """Copy Next standalone static/public assets only when their contents changed."""
    server_path = _standalone_server_path(site_dir)
    if not server_path.is_file():
        raise RuntimeError("The standalone server output is missing; build the requested MAIC page before launch.")

    standalone_dir = server_path.parent
    asset_roots = (
        (site_dir / "public", standalone_dir / "public"),
        (site_dir / ".next" / "static", standalone_dir / ".next" / "static"),
    )
    entries = _standalone_asset_entries(site_dir)
    fingerprint = _standalone_assets_fingerprint(entries, site_dir)
    stamp_path = standalone_dir / ".campus-suite-assets.json"

    # All generated destinations must remain under this site's standalone root.
    # This also rejects a junction/symlinked .next/standalone parent that points
    # outside the workspace before any recursive cleanup or copy is attempted.
    if not _path_is_within(standalone_dir, site_dir):
        raise RuntimeError("The standalone output path escapes the MAIC workspace.")
    standalone_root = standalone_dir.resolve()
    for _source_dir, target_dir in asset_roots:
        if not _path_is_within(target_dir.parent, standalone_root, allow_equal=True):
            raise RuntimeError("A standalone asset destination escapes the generated output directory.")

    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        current = stamp.get("fingerprint") == fingerprint and all(
            target.is_file() and target.stat().st_size == source.stat().st_size
            for _label, source, target in entries
        )
    except (OSError, json.JSONDecodeError, AttributeError):
        current = False
    if current:
        return False

    # Remove only the generated asset destinations. Keeping stale hashed chunks
    # would waste disk and can hide missing assets after a source deletion.
    for _source_dir, target_dir in asset_roots:
        if target_dir.is_symlink():
            target_dir.unlink()
        elif target_dir.is_dir():
            if not _path_is_within(target_dir, standalone_root):
                raise RuntimeError("A standalone asset directory resolves outside the generated output directory.")
            shutil.rmtree(target_dir)
        elif target_dir.exists():
            target_dir.unlink()
    for source_dir, target_dir in asset_roots:
        if source_dir.is_dir():
            shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)

    temporary_stamp = stamp_path.with_suffix(".tmp")
    if temporary_stamp.is_symlink():
        temporary_stamp.unlink()
    temporary_stamp.write_text(json.dumps({"fingerprint": fingerprint}), encoding="utf-8")
    temporary_stamp.replace(stamp_path)
    return True


def _prepare_agent_workspace_build(
    site_dir: Path, node_binary: str, next_cli: Path, env: dict[str, str]
) -> bool:
    """Build the agent UI once and reuse its production output until sources change."""
    build_dir = site_dir / ".next"
    build_id = build_dir / "BUILD_ID"
    stamp_path = build_dir / "campus-agent-build.json"
    fingerprint = _agent_workspace_build_fingerprint(site_dir)
    standalone_server = _standalone_server_path(site_dir)

    def is_current() -> bool:
        try:
            stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
            return (
                build_id.is_file()
                and standalone_server.is_file()
                and stamp.get("fingerprint") == fingerprint
            )
        except (OSError, json.JSONDecodeError, AttributeError):
            return False

    if is_current():
        return False

    with _agent_workspace_build_lock(site_dir):
        if is_current():
            return False
        build_log = ROOT / "logs" / "agent-workspace-build.log"
        build_log.parent.mkdir(parents=True, exist_ok=True)
        with build_log.open("w", encoding="utf-8") as output:
            result = subprocess.run(
                [node_binary, str(next_cli), "build", "--webpack"],
                cwd=site_dir,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if result.returncode != 0 or not build_id.is_file() or not standalone_server.is_file():
            details = build_log.read_text(encoding="utf-8", errors="replace")[-2400:]
            raise RuntimeError(
                "Campus Suite could not prepare the Agent Workspace. "
                f"See logs/agent-workspace-build.log for details.\n{details}"
            )

        temporary_stamp = stamp_path.with_suffix(".tmp")
        temporary_stamp.write_text(json.dumps({"fingerprint": fingerprint}), encoding="utf-8")
        temporary_stamp.replace(stamp_path)
        return True


@contextmanager
def _agent_workspace_build_lock(site_dir: Path):
    """Serialize builds from multiple Workspace windows without touching .next."""
    lock_key = hashlib.sha256(str(site_dir.resolve()).encode("utf-8")).hexdigest()[:24]
    lock_path = Path(tempfile.gettempdir()) / f"campus-agent-build-{lock_key}.lock"
    lock_handle = lock_path.open("a+b")
    try:
        lock_handle.seek(0, os.SEEK_END)
        if lock_handle.tell() == 0:
            lock_handle.write(b"0")
            lock_handle.flush()
        deadline = time.monotonic() + 600
        while True:
            try:
                lock_handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Another Campus Agent Workspace build is still running.")
                time.sleep(0.25)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                lock_handle.seek(0)
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock_handle.close()


def _port_is_open(port: str) -> bool:
    try:
        with socket.create_connection((LOCAL_BIND_HOST, int(port)), timeout=0.2):
            return True
    except (OSError, ValueError):
        return False


def _can_bind_loopback_port(port: str) -> bool:
    """Check whether this desktop server can bind its loopback address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((LOCAL_BIND_HOST, int(port)))
        return True
    except (OSError, ValueError):
        return False


def _find_available_server_port(preferred_port: str, *, agent_workspace: bool) -> str:
    """Find a free port in this app's range without taking reserved services."""
    try:
        base_port = int(preferred_port)
    except ValueError as exc:
        raise RuntimeError(f"Invalid MAIC server port: {preferred_port}") from exc

    # Keep classroom and agent servers in separate ranges. The AI gateway and
    # the other server's default port are reserved even when a workspace falls
    # back because its preferred port is occupied.
    offset = 100 if agent_workspace else 2
    reserved = {int(AGENT_PORT), int(CAMPUS_AI_PORT)}
    candidates = range(base_port + offset, min(base_port + offset + 98, 65536))
    for candidate in candidates:
        if candidate in reserved or candidate == base_port:
            continue
        candidate_port = str(candidate)
        if _can_bind_loopback_port(candidate_port):
            return candidate_port
    app_name = "Agent Workspace" if agent_workspace else "MAIC"
    raise RuntimeError(f"No free local port is available for {app_name}.")


def _parse_listener_argv(command: str, *, windows: bool = False) -> list[str]:
    if not command:
        return []
    try:
        arguments = shlex.split(command, posix=not windows)
    except ValueError:
        return []
    return [argument.strip('"') for argument in arguments]


def _is_expected_next_entrypoint(
    argv: object, command: str, expected_paths: set[str], normalized,
) -> bool:
    if isinstance(argv, (list, tuple)) and len(argv) > 1:
        entrypoint = normalized(str(argv[1]).strip('"'))
        if entrypoint in expected_paths:
            return True

    # macOS `ps -o command=` flattens argv and can omit quotes around a path
    # containing spaces. Accept only an exact path immediately after the Node
    # executable token; never accept a matching substring in another argument.
    if sys.platform != "darwin":
        return False
    flattened = normalized(command)
    for expected in expected_paths:
        path = normalized(expected)
        start = flattened.find(path)
        while start >= 0:
            end = start + len(path)
            left_ok = start == 0 or flattened[start - 1].isspace() or flattened[start - 1] in {'"', "'"}
            right_ok = end == len(flattened) or flattened[end].isspace() or flattened[end] in {'"', "'"}
            if left_ok and right_ok:
                prefix = flattened[:start].rstrip()
                if prefix.endswith(('"', "'")):
                    prefix = prefix[:-1].rstrip()
                prefix_argv = _parse_listener_argv(prefix)
                if len(prefix_argv) == 1 and Path(prefix_argv[0]).name.lower() in {"node", "node.exe"}:
                    return True
            start = flattened.find(path, start + 1)
    return False


def _listener_details(port: str) -> list[dict[str, object]]:
    """Return listening addresses and process identity for an occupied port.

    This deliberately fails closed on platforms where the listener cannot be
    inspected. A desktop launcher must not adopt an unverifiable process.
    """
    port_number = int(port)
    if not 1 <= port_number <= 65535:
        raise ValueError(f"Invalid local server port: {port}")

    if os.name == "nt":
        # Netstat is quick and gives the precise bind address. Query WMI only
        # for PIDs actually listening on this port, so the unoccupied-port
        # case does not pay the startup cost of PowerShell/CIM.
        netstat = shutil.which("netstat.exe") or shutil.which("netstat")
        if not netstat:
            raise RuntimeError("Cannot verify the process listening on the MAIC port (netstat is unavailable).")
        netstat_result = subprocess.run(
            [netstat, "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if netstat_result.returncode != 0:
            raise RuntimeError("Cannot inspect the process listening on the MAIC port.")
        listeners = []
        for line in netstat_result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 5 or fields[0].upper() != "TCP" or fields[3].upper() != "LISTENING":
                continue
            local_address, separator, local_port = fields[1].rpartition(":")
            if not separator or local_port != str(port_number):
                continue
            listeners.append({"address": local_address.strip("[]"), "pid": fields[4]})
        if not listeners:
            return []

        commands: dict[str, str] = {}
        argument_vectors: dict[str, list[str]] = {}
        working_directories: dict[str, str] = {}
        try:
            import psutil
        except ImportError:
            psutil = None
        if psutil is not None:
            for pid in {item["pid"] for item in listeners if item["pid"].isdigit()}:
                try:
                    process = psutil.Process(int(pid))
                    argument_vectors[pid] = [str(part) for part in process.cmdline()]
                    commands[pid] = subprocess.list2cmdline(argument_vectors[pid])
                    working_directories[pid] = process.cwd()
                except (psutil.Error, OSError):
                    pass

        unresolved_pids = sorted(
            {item["pid"] for item in listeners if item["pid"].isdigit() and not commands.get(item["pid"])}
        )
        if not unresolved_pids:
            return [
                {
                    "address": item["address"],
                    "command": commands.get(item["pid"], ""),
                    "argv": argument_vectors.get(item["pid"], []),
                    "cwd": working_directories.get(item["pid"], ""),
                }
                for item in listeners
            ]

        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            return [
                {
                    "address": item["address"],
                    "command": commands.get(item["pid"], ""),
                    "argv": argument_vectors.get(item["pid"], []),
                    "cwd": working_directories.get(item["pid"], ""),
                }
                for item in listeners
            ]
        pid_filter = " OR ".join(f"ProcessId = {pid}" for pid in unresolved_pids)
        script = (
            f"$rows = @(Get-CimInstance Win32_Process -Filter '{pid_filter}' "
            "-ErrorAction SilentlyContinue | Select-Object ProcessId,CommandLine); "
            "if ($rows.Count -eq 0) { '[]' } else { $rows | ConvertTo-Json -Compress }"
        )
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("Cannot inspect the process listening on the MAIC port.")
        try:
            rows = json.loads(result.stdout.strip() or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Cannot inspect the process listening on the MAIC port.") from exc
        if isinstance(rows, dict):
            rows = [rows]
        commands.update(
            {
                str(row.get("ProcessId", "")): str(row.get("CommandLine", ""))
                for row in rows
                if isinstance(row, dict)
            }
        )
        argument_vectors.update(
            {
                pid: _parse_listener_argv(command, windows=True)
                for pid, command in commands.items()
                if pid not in argument_vectors
            }
        )
        return [
            {
                "address": item["address"],
                "command": commands.get(item["pid"], ""),
                "argv": argument_vectors.get(item["pid"], []),
                "cwd": working_directories.get(item["pid"], ""),
            }
            for item in listeners
        ]

    if sys.platform.startswith("linux"):
        ss = shutil.which("ss")
        if not ss:
            raise RuntimeError("Cannot verify the process listening on the MAIC port (ss is unavailable).")
        result = subprocess.run(
            [ss, "-H", "-ltnp", "sport", "=", f":{port_number}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("Cannot inspect the process listening on the MAIC port.")
        listeners = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 4:
                continue
            address = fields[3].rsplit(":", 1)[0].strip("[]")
            pid_match = re.search(r"pid=(\d+)", line)
            pid = pid_match.group(1) if pid_match else ""
            command = cwd = ""
            argv: list[str] = []
            if pid:
                proc_dir = Path("/proc") / pid
                try:
                    raw_arguments = (proc_dir / "cmdline").read_bytes().split(b"\0")
                    argv = [part.decode(errors="replace") for part in raw_arguments if part]
                    command = subprocess.list2cmdline(argv)
                    cwd = str((proc_dir / "cwd").resolve())
                except OSError:
                    pass
            listeners.append({"address": address, "command": command, "argv": argv, "cwd": cwd})
        return listeners

    if sys.platform == "darwin":
        lsof = shutil.which("lsof")
        if not lsof:
            raise RuntimeError("Cannot verify the process listening on the MAIC port (lsof is unavailable).")
        result = subprocess.run(
            [lsof, "-nP", "-iTCP:" + str(port_number), "-sTCP:LISTEN", "-Fpcn"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode not in (0, 1):
            raise RuntimeError("Cannot inspect the process listening on the MAIC port.")
        listeners = []
        current: dict[str, object] | None = None
        for line in result.stdout.splitlines():
            if line.startswith("p"):
                if current:
                    listeners.append(current)
                current = {"pid": line[1:], "address": "", "command": "", "argv": [], "cwd": ""}
            elif current and line.startswith("n"):
                endpoint = line[1:].split("->", 1)[0]
                current["address"] = endpoint.rsplit(":", 1)[0].strip("[]")
        if current:
            listeners.append(current)
        for listener in listeners:
            pid = str(listener.pop("pid"))
            command = subprocess.run(
                ["ps", "-p", pid, "-o", "command="], capture_output=True, text=True, timeout=5, check=False
            )
            cwd = subprocess.run(
                [lsof, "-a", "-p", pid, "-d", "cwd", "-Fn"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            listener["command"] = command.stdout.strip() if command.returncode == 0 else ""
            listener["argv"] = _parse_listener_argv(str(listener["command"]))
            listener["cwd"] = next((line[1:] for line in cwd.stdout.splitlines() if line.startswith("n")), "")
        return listeners

    raise RuntimeError("Cannot verify the process listening on the MAIC port on this platform.")


def _assert_existing_server_is_maic(port: str, site_dir: Path, next_cli: Path) -> None:
    """Reject occupied ports unless all listeners are loopback MAIC processes."""
    try:
        listeners = _listener_details(port)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError(
            f"Port {port} is occupied, but Campus Suite could not verify its listener. Close it before launching MAIC."
        ) from exc
    if not listeners:
        raise RuntimeError(f"Port {port} is occupied, but its listener could not be identified. Close it before launching MAIC.")

    standalone_server = _standalone_server_path(site_dir)
    expected_paths = {str(next_cli), str(standalone_server)}

    def normalized(value: str) -> str:
        return os.path.normcase(os.path.normpath(value)).replace("/", "\\")

    # Next's generated standalone server changes its working directory to the
    # directory containing server.js. Dev servers keep the source directory.
    expected_cwds = {
        normalized(str(site_dir.resolve())),
        normalized(str(standalone_server.parent.resolve())),
    }
    for listener in listeners:
        address = str(listener.get("address", "")).split("%", 1)[0]
        try:
            if not ipaddress.ip_address(address).is_loopback:
                raise RuntimeError(
                    f"Port {port} is bound beyond loopback. Close that listener before launching MAIC."
                )
        except ValueError as exc:
            raise RuntimeError(f"Port {port} has an unverifiable bind address. Close that listener before launching MAIC.") from exc
        command = str(listener.get("command", ""))
        argv = listener.get("argv")
        if not isinstance(argv, (list, tuple)):
            argv = _parse_listener_argv(command, windows=os.name == "nt")
        # MAIC launches `node <next-cli>` or `node <standalone-server>`.
        # Require that exact entrypoint position, rather than a path substring
        # somewhere in an unrelated process's flattened command line.
        if not _is_expected_next_entrypoint(
            argv, command, {normalized(path) for path in expected_paths}, normalized,
        ):
            raise RuntimeError(f"Port {port} belongs to an unrelated process. Close it before launching MAIC.")
        cwd = str(listener.get("cwd", ""))
        if cwd and normalized(cwd) not in expected_cwds:
            raise RuntimeError(f"Port {port} belongs to a different MAIC workspace. Close it before launching MAIC.")


def _stop_failed_server(process: subprocess.Popen, *, terminate_timeout: float = 5, kill_timeout: float = 5) -> None:
    """Stop a failed startup process, escalating to kill and reaping it."""
    if process.poll() is not None:
        return
    terminate_sent = True
    try:
        process.terminate()
    except OSError:
        if process.poll() is not None:
            return
        terminate_sent = False
    if terminate_sent:
        try:
            process.wait(timeout=terminate_timeout)
            return
        except subprocess.TimeoutExpired:
            pass
    try:
        process.kill()
    except OSError:
        if process.poll() is not None:
            return
        raise RuntimeError("MAIC server could not be killed after a startup failure.")
    try:
        process.wait(timeout=kill_timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("MAIC server did not exit after kill; its port may remain occupied.") from exc


def _campus_gateway_is_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status == 200 and payload.get("provider") == "campus-suite-key-manager" and payload.get("service_version") == 2
    except (OSError, ValueError):
        return False


@contextmanager
def _server_start_lock(port: str):
    """Serialize concurrent desktop launches for one local Next.js port."""
    lock_path = Path(tempfile.gettempdir()) / f"campus-suite-start-{port}.lock"
    lock_handle = lock_path.open("a+b")
    try:
        lock_handle.seek(0, os.SEEK_END)
        if lock_handle.tell() == 0:
            lock_handle.write(b"0")
            lock_handle.flush()
        deadline = time.monotonic() + 330
        while True:
            try:
                lock_handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Another Campus Suite launch is still preparing port {port}.")
                time.sleep(0.25)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                lock_handle.seek(0)
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock_handle.close()


def start_maic_server(
    *,
    ready_path: str = "/",
    production_only: bool = False,
    fast_startup: bool = False,
) -> tuple[subprocess.Popen | None, str]:
    """Start or reuse the requested local app without racing another window."""
    port = AGENT_PORT if ready_path == "/agents" else PORT
    with _server_start_lock(port):
        return _start_maic_server_unlocked(
            ready_path=ready_path,
            production_only=production_only,
            fast_startup=fast_startup,
        )


def _start_maic_server_unlocked(
    *,
    ready_path: str = "/",
    production_only: bool = False,
    fast_startup: bool = False,
) -> tuple[subprocess.Popen | None, str]:
    global _gateway_process
    if not (MAIC_DIR / "package.json").exists():
        raise RuntimeError("MAIC Classroom files are missing from the Campus Suite installation.")
    agent_workspace = ready_path == "/agents"
    site_dir = MAIC_DIR / "campus-agent-shell" if agent_workspace else MAIC_DIR
    if not (site_dir / "package.json").exists():
        raise RuntimeError("Campus Agent Workspace files are missing from the Campus Suite installation.")
    server_port = AGENT_PORT if agent_workspace else PORT
    preferred_server_port = server_port
    env = os.environ.copy()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env["PATH"] = str(MAIC_DIR / "scripts") + os.pathsep + env.get("PATH", "")
    inherit_provider_settings(env)
    env["PORT"] = server_port
    env["CAMPUS_SUITE_AGENT_HISTORY_DIR"] = str(ROOT / "history" / "agents")
    env["CAMPUS_THEME_PREFERENCE_PATH"] = str(ROOT / "theme-preference.json")
    env.setdefault("CAMPUS_SUITE_MEMORY_PATH", str(ROOT / "storage" / "unified_memory.json"))
    if agent_workspace:
        env["CAMPUS_AGENT_WORKSPACE"] = "true"
    # Keep local Kokoro TTS on the Python installation where it is installed.
    env.setdefault("KOKORO_PYTHON", sys.executable)
    configure_maic_provider_router(env)
    env["NEXT_TELEMETRY_DISABLED"] = "1"
    # Desktop-launched Next servers must never listen on LAN interfaces. The
    # standalone entrypoint consumes HOSTNAME directly; next dev also receives
    # its documented --hostname option below.
    _configure_next_server_environment(env)
    gateway_url = f"http://127.0.0.1:{CAMPUS_AI_PORT}/health"
    if not _port_is_open(CAMPUS_AI_PORT):
        _gateway_process = subprocess.Popen(
            [sys.executable, str(ROOT / "campus_ai_gateway.py")],
            cwd=ROOT,
            env=env,
            creationflags=creationflags,
        )
        atexit.register(_stop_gateway)
        if not fast_startup:
            for _ in range(15):
                if _campus_gateway_is_ready(gateway_url):
                    break
                if _gateway_process.poll() is not None:
                    raise RuntimeError("Campus AI gateway exited during startup. Check the Python runtime and key manager.")
                time.sleep(0.5)
            else:
                _stop_gateway()
                raise RuntimeError("Campus AI gateway did not become ready within 7.5 seconds.")
    elif not fast_startup and not _campus_gateway_is_ready(gateway_url):
        raise RuntimeError("An older or unrelated service is using the Campus AI port. Close it, then restart Campus Suite.")
    build_id = site_dir / ".next" / "BUILD_ID"
    # Next dev chunks are origin-sensitive, so dev mode uses localhost. A
    # production standalone server is explicitly bound to IPv4 loopback; use
    # that same address because some Windows WebViews resolve localhost to IPv6.
    server_host = "127.0.0.1" if build_id.exists() else "localhost"
    url = f"http://{server_host}:{server_port}"
    ready_url = url + ready_path
    # Don't mistake an unrelated app already bound to this port for MAIC.
    expected_text = "Study Companion" if agent_workspace else "Interactive Classroom"
    next_cli = MAIC_DIR / "node_modules" / "next" / "dist" / "bin" / "next"
    existing_server = _port_is_open(server_port) or not _can_bind_loopback_port(server_port)
    if existing_server:
        try:
            _assert_existing_server_is_maic(server_port, site_dir, next_cli)
        except RuntimeError:
            # Another workspace or unrelated local service may own the
            # preferred port. The desktop consumes this returned URL, so give
            # this launch its own loopback port instead of forcing the user to
            # stop the other workspace.
            server_port = _find_available_server_port(
                preferred_server_port, agent_workspace=agent_workspace,
            )
            env["PORT"] = server_port
            url = f"http://{server_host}:{server_port}"
            ready_url = url + ready_path
            existing_server = False
        else:
            if _server_is_ready(ready_url, timeout=1, expected_text=expected_text):
                record_activity("campus-suite", "reused-maic", {"port": server_port})
                return None, url
    # A verified MAIC listener can still be compiling its route. Wait for the
    # page itself and avoid launching a second Next process on the occupied port.
    if not build_id.exists() and production_only:
        raise RuntimeError("The production build is missing. Build the requested Campus Suite page before opening it.")
    if not existing_server and not (MAIC_DIR / "node_modules" / ".bin" / "next.cmd").exists():
        # The project pins pnpm in package.json; Corepack avoids broken global shims.
        install = subprocess.run(corepack_pnpm("install", "--frozen-lockfile"), cwd=MAIC_DIR, env=env)
        if install.returncode:
            raise RuntimeError("MAIC dependencies could not be installed. See the terminal output above.")
    # Production uses Next's standalone server output because this workspace
    # sets output: "standalone" in its Next config.
    node_binary = shutil.which("node")
    if not node_binary or not next_cli.exists():
        raise RuntimeError("Node.js or the MAIC Next.js runtime is unavailable.")
    if agent_workspace and not existing_server:
        _prepare_agent_workspace_build(site_dir, node_binary, next_cli, env)
        build_id = site_dir / ".next" / "BUILD_ID"
    command = "start" if build_id.exists() else "dev"
    # The standalone production server is explicitly bound to IPv4 loopback.
    # On some Windows setups, `localhost` resolves to IPv6 first, so a desktop
    # readiness probe can time out even though the server is serving on 127.0.0.1.
    # Keep localhost for dev mode because Next's dev chunks are origin-sensitive.
    server_host = "127.0.0.1" if command == "start" else "localhost"
    url = f"http://{server_host}:{server_port}"
    ready_url = url + ready_path
    log_name = "agent-workspace-server.log" if agent_workspace else "maic-server.log"
    log_path = ROOT / "logs" / log_name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    process = None
    if not existing_server:
        # Recheck after dependency/build preparation. Another process may have
        # claimed the port while those operations were running.
        if _port_is_open(server_port) or not _can_bind_loopback_port(server_port):
            try:
                _assert_existing_server_is_maic(server_port, site_dir, next_cli)
            except RuntimeError:
                server_port = _find_available_server_port(
                    preferred_server_port, agent_workspace=agent_workspace,
                )
                env["PORT"] = server_port
                url = f"http://{server_host}:{server_port}"
                ready_url = url + ready_path
                existing_server = False
            else:
                existing_server = True
        if not existing_server:
            with log_path.open("a", encoding="utf-8") as server_log:
                if command == "start":
                    standalone_server = _standalone_server_path(site_dir)
                    if not standalone_server.is_file():
                        raise RuntimeError("The standalone server output is missing. Rebuild the Campus Suite page and try again.")
                    _prepare_standalone_assets(site_dir)
                else:
                    # Turbopack has been panicking while restoring the shared CSS
                    # filesystem cache on Windows. Use Next's supported webpack
                    # dev mode for the desktop launcher until that cache path is
                    # stable; production builds continue to use their normal output.
                    standalone_server = _standalone_server_path(site_dir)
                command_args = _next_server_command(node_binary, next_cli, command, standalone_server)
                process = subprocess.Popen(
                    command_args,
                    cwd=site_dir,
                    env=env,
                    creationflags=creationflags,
                    stdout=server_log,
                    stderr=subprocess.STDOUT,
                )
    # The MAIC root route can take more than two minutes to compile on a cold
    # Windows checkout, just like the larger Agent Workspace route.
    startup_timeout = 300
    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        # The native Agent Workspace window can render while its API gateway or
        # first page request is still warming up. Waiting for `/agents` here
        # made a slow render fail the entire launch and replaced the window
        # with an error screen. In fast mode, hand the URL to the WebView once
        # the local Next server is listening; the page can finish loading there.
        if fast_startup and _port_is_open(server_port):
            record_activity("campus-suite", "reused-maic" if existing_server else "opened-maic", {"port": server_port})
            return process, url
        if (
            _campus_gateway_is_ready(gateway_url)
            and _server_is_ready(ready_url, timeout=4, expected_text=expected_text)
        ):
            record_activity("campus-suite", "reused-maic" if existing_server else "opened-maic", {"port": server_port})
            return process, url
        if not fast_startup and _gateway_process is not None and _gateway_process.poll() is not None:
            raise RuntimeError("Campus AI gateway exited during startup. Check the Python runtime and key manager.")
        if process is not None and process.poll() is not None:
            details = log_path.read_text(encoding="utf-8", errors="replace")[-1600:]
            raise RuntimeError("MAIC server exited during startup. " + (details or f"See logs/{log_name}."))
        time.sleep(0.5)
    if process is not None:
        _stop_failed_server(process)
    raise RuntimeError(
        f"{ready_path} did not finish loading on port {server_port} within {startup_timeout} seconds. "
        f"Check logs/{log_name} for the server error."
    )


def _stop_gateway(*, terminate_timeout: float = 5, kill_timeout: float = 5) -> None:
    global _gateway_process
    process = _gateway_process
    if process is None:
        return
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            if process.poll() is None:
                raise
        try:
            process.wait(timeout=terminate_timeout)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                if process.poll() is None:
                    raise
            process.wait(timeout=kill_timeout)
    if process.poll() is not None:
        _gateway_process = None


def main() -> None:
    _process, url = start_maic_server()
    print(f"MAIC is running at {url}. Start maic_desktop.py for the native app window.")


if __name__ == "__main__":
    main()
