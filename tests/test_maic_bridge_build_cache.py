import os
from types import SimpleNamespace

import maic_bridge
import pytest


def test_agent_workspace_build_is_reused_until_sources_change(tmp_path, monkeypatch):
    root = tmp_path / "project"
    site_dir = root / "MAIC" / "campus-agent-shell"
    monkeypatch.setattr(maic_bridge, "MAIC_DIR", root / "MAIC")
    monkeypatch.setattr(maic_bridge, "ROOT", root)
    (site_dir / "app").mkdir(parents=True)
    (site_dir / "app" / "page.tsx").write_text("export default function Page() {}", encoding="utf-8")
    (site_dir / "public").mkdir()
    (site_dir / "public" / "icon.svg").write_text("<svg/>", encoding="utf-8")
    large_asset = site_dir / "public" / "font.woff2"
    large_asset.write_bytes(b"A" * (300 * 1024))
    (site_dir / "next.config.ts").write_text("export default {}", encoding="utf-8")
    lockfile = root / "MAIC" / "pnpm-lock.yaml"
    lockfile.write_text("lockfileVersion: '1'\n", encoding="utf-8")
    (root / "MAIC" / "tsconfig.json").write_text('{"compilerOptions":{"strict":true}}\n', encoding="utf-8")
    component = root / "MAIC" / "components" / "campus-agent-workspace.tsx"
    component.parent.mkdir(parents=True)
    component.write_text("export const agents = ['study']", encoding="utf-8")
    build_calls = []

    def fake_build(command, **kwargs):
        build_calls.append((command, kwargs["cwd"]))
        build_dir = site_dir / ".next"
        build_dir.mkdir(exist_ok=True)
        (build_dir / "BUILD_ID").write_text(f"build-{len(build_calls)}", encoding="utf-8")
        standalone_server = maic_bridge._standalone_server_path(site_dir)
        standalone_server.parent.mkdir(parents=True, exist_ok=True)
        (standalone_server.parent / ".next" / "static").mkdir(parents=True, exist_ok=True)
        standalone_server.write_text("// server", encoding="utf-8")
        (build_dir / "static").mkdir(exist_ok=True)
        (build_dir / "static" / "chunk.js").write_text("// chunk", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(maic_bridge.subprocess, "run", fake_build)

    assert maic_bridge._prepare_agent_workspace_build(site_dir, "node", root / "next", {}) is True
    assert maic_bridge._prepare_standalone_assets(site_dir) is True
    assert maic_bridge._prepare_agent_workspace_build(site_dir, "node", root / "next", {}) is False
    assert maic_bridge._prepare_standalone_assets(site_dir) is False
    assert len(build_calls) == 1
    standalone_server = maic_bridge._standalone_server_path(site_dir)
    standalone_dir = standalone_server.parent
    assert (standalone_dir / "public" / "icon.svg").is_file()
    assert (standalone_dir / ".next" / "static" / "chunk.js").is_file()

    # Public logo/media changes invalidate the cached build and are recopied.
    public_svg = site_dir / "public" / "icon.svg"
    public_svg.write_text("<svg><title>Updated logo</title></svg>", encoding="utf-8")
    assert maic_bridge._prepare_agent_workspace_build(site_dir, "node", root / "next", {}) is True
    assert maic_bridge._prepare_standalone_assets(site_dir) is True
    assert len(build_calls) == 2
    assert (standalone_dir / "public" / "icon.svg").read_text(encoding="utf-8") == public_svg.read_text(encoding="utf-8")

    # Large public assets are content-hashed too, even if an edit preserves
    # both the file length and its original modification time.
    original_stat = large_asset.stat()
    changed_asset = b"B" * original_stat.st_size
    large_asset.write_bytes(changed_asset)
    os.utime(large_asset, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert large_asset.stat().st_size == original_stat.st_size
    assert large_asset.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert maic_bridge._prepare_agent_workspace_build(site_dir, "node", root / "next", {}) is True
    assert maic_bridge._prepare_standalone_assets(site_dir) is True
    assert len(build_calls) == 3
    assert (standalone_dir / "public" / "font.woff2").read_bytes() == changed_asset

    # Lockfile content changes invalidate the cache even when metadata is
    # restored, so dependency changes cannot reuse an old standalone build.
    lock_stat = lockfile.stat()
    lockfile.write_text("lockfileVersion: '2'\n", encoding="utf-8")
    os.utime(lockfile, ns=(lock_stat.st_atime_ns, lock_stat.st_mtime_ns))
    assert lockfile.stat().st_size == lock_stat.st_size
    assert lockfile.stat().st_mtime_ns == lock_stat.st_mtime_ns
    assert maic_bridge._prepare_agent_workspace_build(site_dir, "node", root / "next", {}) is True
    assert len(build_calls) == 4

    # A matching fingerprint and BUILD_ID are not enough if standalone output
    # disappeared; force a rebuild instead of skipping straight to launch error.
    standalone_server.unlink()
    assert maic_bridge._prepare_agent_workspace_build(site_dir, "node", root / "next", {}) is True
    assert len(build_calls) == 5
    assert standalone_server.is_file()

    component.write_text("export const agents = ['study', 'personal-growth']", encoding="utf-8")
    assert maic_bridge._prepare_agent_workspace_build(site_dir, "node", root / "next", {}) is True
    assert len(build_calls) == 6
    assert all(call[0][-2:] == ["build", "--webpack"] for call in build_calls)


def test_existing_listener_must_be_loopback_and_owned_by_this_maic_workspace(tmp_path, monkeypatch):
    site_dir = tmp_path / "MAIC"
    monkeypatch.setattr(maic_bridge, "MAIC_DIR", site_dir)
    next_cli = site_dir / "node_modules" / "next" / "dist" / "bin" / "next"
    valid_command = f'node "{next_cli}" dev'
    valid_cwd = str(site_dir)
    monkeypatch.setattr(
        maic_bridge,
        "_listener_details",
        lambda _port: [{"address": "127.0.0.1", "command": valid_command, "cwd": valid_cwd}],
    )
    maic_bridge._assert_existing_server_is_maic("3100", site_dir, next_cli)

    unsafe_listeners = [
        {"address": "0.0.0.0", "command": valid_command, "cwd": valid_cwd},
        {"address": "127.0.0.1", "command": "node unrelated-server.js", "cwd": valid_cwd},
        {
            "address": "127.0.0.1",
            "command": f'node unrelated-server.js --next-cli "{next_cli}"',
            "cwd": valid_cwd,
        },
        {
            "address": "127.0.0.1",
            "argv": ["node", "unrelated-server.js", "--next-cli", str(next_cli)],
            "command": f'node unrelated-server.js --next-cli "{next_cli}"',
            "cwd": valid_cwd,
        },
        {"address": "127.0.0.1", "command": valid_command, "cwd": str(tmp_path / "Other MAIC")},
    ]
    for listener in unsafe_listeners:
        monkeypatch.setattr(maic_bridge, "_listener_details", lambda _port, row=listener: [row])
        with pytest.raises(RuntimeError):
            maic_bridge._assert_existing_server_is_maic("3100", site_dir, next_cli)

    standalone = maic_bridge._standalone_server_path(site_dir)
    monkeypatch.setattr(
        maic_bridge,
        "_listener_details",
        lambda _port: [{"address": "::1", "argv": ["node", str(standalone)], "cwd": valid_cwd}],
    )
    maic_bridge._assert_existing_server_is_maic("3100", site_dir, next_cli)


def test_startup_does_not_reuse_a_ready_wildcard_listener(tmp_path, monkeypatch):
    root = tmp_path / "project"
    maic_dir = root / "MAIC"
    maic_dir.mkdir(parents=True)
    (maic_dir / "package.json").write_text("{}", encoding="utf-8")
    next_cli = maic_dir / "node_modules" / "next" / "dist" / "bin" / "next"
    next_cli.parent.mkdir(parents=True)
    next_cli.write_text("", encoding="utf-8")
    next_bin = maic_dir / "node_modules" / ".bin"
    next_bin.mkdir(parents=True)
    (next_bin / "next.cmd").write_text("", encoding="utf-8")
    monkeypatch.setattr(maic_bridge, "ROOT", root)
    monkeypatch.setattr(maic_bridge, "MAIC_DIR", maic_dir)
    monkeypatch.setattr(maic_bridge, "PORT", "3100")
    monkeypatch.setattr(maic_bridge, "CAMPUS_AI_PORT", "3110")
    monkeypatch.setattr(maic_bridge, "_port_is_open", lambda port: port in {"3100", "3110"})
    monkeypatch.setattr(maic_bridge, "_can_bind_loopback_port", lambda port: port != "3100")
    monkeypatch.setattr(maic_bridge, "_campus_gateway_is_ready", lambda _url: True)
    monkeypatch.setattr(maic_bridge, "_find_available_server_port", lambda *_args, **_kwargs: "3101")
    monkeypatch.setattr(
        maic_bridge,
        "_listener_details",
        lambda _port: [{
            "address": "0.0.0.0",
            "command": f'node "{maic_dir / "node_modules" / "next" / "dist" / "bin" / "next"}" dev',
            "cwd": str(maic_dir),
        }],
    )
    ready_urls = []
    monkeypatch.setattr(
        maic_bridge,
        "_server_is_ready",
        lambda url, **_kwargs: ready_urls.append(url) or True,
    )
    monkeypatch.setattr(
        maic_bridge.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(poll=lambda: None),
    )

    _process, url = maic_bridge._start_maic_server_unlocked()

    assert url == "http://localhost:3101"
    assert ready_urls == ["http://localhost:3101/"]


def test_failed_startup_terminates_then_kills_and_reaps_server():
    class StuckProcess:
        def __init__(self):
            self.events = []
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.events.append("terminate")

        def wait(self, timeout):
            self.events.append(("wait", timeout))
            if self.running:
                raise maic_bridge.subprocess.TimeoutExpired("node", timeout)

        def kill(self):
            self.events.append("kill")
            self.running = False

    process = StuckProcess()
    maic_bridge._stop_failed_server(process, terminate_timeout=0.1, kill_timeout=0.2)
    assert process.events == ["terminate", ("wait", 0.1), "kill", ("wait", 0.2)]
    assert process.poll() == 0


def test_failed_startup_kills_server_if_terminate_fails():
    class TerminateDeniedProcess:
        def __init__(self):
            self.events = []
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.events.append("terminate")
            raise PermissionError("terminate denied")

        def kill(self):
            self.events.append("kill")
            self.running = False

        def wait(self, timeout):
            self.events.append(("wait", timeout))

    process = TerminateDeniedProcess()
    maic_bridge._stop_failed_server(process, terminate_timeout=0.1, kill_timeout=0.2)
    assert process.events == ["terminate", "kill", ("wait", 0.2)]
    assert process.poll() == 0


def test_gateway_cleanup_waits_and_reaps_after_kill(monkeypatch):
    class StuckGateway:
        def __init__(self):
            self.events = []
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.events.append("terminate")

        def wait(self, timeout):
            self.events.append(("wait", timeout))
            if self.running:
                raise maic_bridge.subprocess.TimeoutExpired("gateway", timeout)

        def kill(self):
            self.events.append("kill")
            self.running = False

    process = StuckGateway()
    monkeypatch.setattr(maic_bridge, "_gateway_process", process)
    maic_bridge._stop_gateway(terminate_timeout=0.1, kill_timeout=0.2)
    assert process.events == ["terminate", ("wait", 0.1), "kill", ("wait", 0.2)]
    assert process.poll() == 0
    assert maic_bridge._gateway_process is None


def test_agent_workspace_build_cache_invalidates_when_parent_tsconfig_changes(tmp_path, monkeypatch):
    root = tmp_path / "project"
    maic_dir = root / "MAIC"
    site_dir = maic_dir / "campus-agent-shell"
    maic_dir.mkdir(parents=True)
    site_dir.mkdir()
    (maic_dir / "tsconfig.json").write_text('{"compilerOptions":{"strict":true}}\n', encoding="utf-8")
    (maic_dir / "pnpm-lock.yaml").write_text("lockfileVersion: '1'\n", encoding="utf-8")
    (site_dir / "app").mkdir()
    (site_dir / "app" / "page.tsx").write_text("export default function Page() {}", encoding="utf-8")
    (site_dir / "package.json").write_text("{}", encoding="utf-8")
    (site_dir / "tsconfig.json").write_text('{"extends":"../tsconfig.json"}', encoding="utf-8")
    (site_dir / "next.config.ts").write_text("export default {}", encoding="utf-8")
    (site_dir / "postcss.config.mjs").write_text("export default {}", encoding="utf-8")
    monkeypatch.setattr(maic_bridge, "MAIC_DIR", maic_dir)
    monkeypatch.setattr(maic_bridge, "ROOT", root)
    build_calls = []

    def fake_build(command, **kwargs):
        build_calls.append(command)
        build_dir = site_dir / ".next"
        build_dir.mkdir(exist_ok=True)
        (build_dir / "BUILD_ID").write_text(str(len(build_calls)), encoding="utf-8")
        server = maic_bridge._standalone_server_path(site_dir)
        server.parent.mkdir(parents=True, exist_ok=True)
        server.write_text("// server", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(maic_bridge.subprocess, "run", fake_build)
    assert maic_bridge._prepare_agent_workspace_build(site_dir, "node", root / "next", {}) is True
    assert maic_bridge._prepare_agent_workspace_build(site_dir, "node", root / "next", {}) is False
    (maic_dir / "tsconfig.json").write_text('{"compilerOptions":{"strict":null}}\n', encoding="utf-8")
    assert maic_bridge._prepare_agent_workspace_build(site_dir, "node", root / "next", {}) is True
    assert len(build_calls) == 2


def test_root_standalone_assets_are_copied_once_and_repaired_if_missing(tmp_path, monkeypatch):
    maic_dir = tmp_path / "MAIC"
    standalone_dir = maic_dir / ".next" / "standalone"
    static_source = maic_dir / ".next" / "static"
    public_source = maic_dir / "public"
    maic_dir.mkdir()
    standalone_dir.mkdir(parents=True)
    (standalone_dir / "server.js").write_text("// standalone", encoding="utf-8")
    (static_source / "chunks").mkdir(parents=True)
    (static_source / "chunks" / "app.js").write_text("static bundle", encoding="utf-8")
    public_source.mkdir()
    (public_source / "brand.svg").write_text("<svg>brand</svg>", encoding="utf-8")
    monkeypatch.setattr(maic_bridge, "MAIC_DIR", maic_dir)

    copy_calls = []
    original_copytree = maic_bridge.shutil.copytree

    def count_copytree(source, destination, *args, **kwargs):
        copy_calls.append((source, destination))
        monkeypatch.setattr(maic_bridge.shutil, "copytree", original_copytree)
        try:
            return original_copytree(source, destination, *args, **kwargs)
        finally:
            monkeypatch.setattr(maic_bridge.shutil, "copytree", count_copytree)

    monkeypatch.setattr(maic_bridge.shutil, "copytree", count_copytree)
    assert maic_bridge._prepare_standalone_assets(maic_dir) is True
    assert (standalone_dir / ".next" / "static" / "chunks" / "app.js").read_text(encoding="utf-8") == "static bundle"
    assert (standalone_dir / "public" / "brand.svg").read_text(encoding="utf-8") == "<svg>brand</svg>"
    assert len(copy_calls) == 2

    assert maic_bridge._prepare_standalone_assets(maic_dir) is False
    assert len(copy_calls) == 2

    (standalone_dir / ".next" / "static" / "chunks" / "app.js").unlink()
    assert maic_bridge._prepare_standalone_assets(maic_dir) is True
    assert len(copy_calls) == 4
    assert (standalone_dir / ".next" / "static" / "chunks" / "app.js").is_file()


def test_standalone_asset_prep_rejects_output_parent_outside_workspace(tmp_path, monkeypatch):
    maic_dir = tmp_path / "MAIC"
    standalone_dir = maic_dir / ".next" / "standalone"
    static_source = maic_dir / ".next" / "static"
    standalone_dir.mkdir(parents=True)
    (standalone_dir / "server.js").write_text("// standalone", encoding="utf-8")
    static_source.mkdir(parents=True)
    (static_source / "app.js").write_text("bundle", encoding="utf-8")
    monkeypatch.setattr(maic_bridge, "MAIC_DIR", maic_dir)
    real_check = maic_bridge._path_is_within
    monkeypatch.setattr(
        maic_bridge,
        "_path_is_within",
        lambda path, parent: False if path == standalone_dir else real_check(path, parent),
    )

    with pytest.raises(RuntimeError, match="escapes the MAIC workspace"):
        maic_bridge._prepare_standalone_assets(maic_dir)

    assert (static_source / "app.js").read_text(encoding="utf-8") == "bundle"


def test_main_production_launch_prepares_root_standalone_assets(tmp_path, monkeypatch):
    root = tmp_path / "project"
    maic_dir = root / "MAIC"
    standalone_dir = maic_dir / ".next" / "standalone"
    server = standalone_dir / "server.js"
    next_cli = maic_dir / "node_modules" / "next" / "dist" / "bin" / "next"
    (maic_dir / "node_modules" / ".bin").mkdir(parents=True)
    (maic_dir / "node_modules" / ".bin" / "next.cmd").write_text("", encoding="utf-8")
    next_cli.parent.mkdir(parents=True)
    next_cli.write_text("", encoding="utf-8")
    standalone_dir.mkdir(parents=True)
    server.write_text("// standalone", encoding="utf-8")
    (maic_dir / ".next").mkdir(exist_ok=True)
    (maic_dir / ".next" / "BUILD_ID").write_text("build-id", encoding="utf-8")
    (maic_dir / "package.json").write_text("{}", encoding="utf-8")
    (maic_dir / ".next" / "static" / "chunks").mkdir(parents=True)
    (maic_dir / ".next" / "static" / "chunks" / "app.js").write_text("static bundle", encoding="utf-8")
    (maic_dir / "public").mkdir()
    (maic_dir / "public" / "brand.svg").write_text("<svg>brand</svg>", encoding="utf-8")
    root.mkdir(exist_ok=True)

    monkeypatch.setattr(maic_bridge, "ROOT", root)
    monkeypatch.setattr(maic_bridge, "MAIC_DIR", maic_dir)
    monkeypatch.setattr(maic_bridge, "PORT", "3100")
    monkeypatch.setattr(maic_bridge, "CAMPUS_AI_PORT", "3110")
    monkeypatch.setattr(maic_bridge, "_port_is_open", lambda _port: False)
    monkeypatch.setattr(maic_bridge, "_gateway_process", None)
    gateway_checks = []

    def gateway_ready(url):
        gateway_checks.append(url)
        return len(gateway_checks) >= 2

    monkeypatch.setattr(maic_bridge, "_campus_gateway_is_ready", gateway_ready)
    monkeypatch.setattr(maic_bridge.shutil, "which", lambda name: "node" if name == "node" else None)
    monkeypatch.setattr(maic_bridge, "_server_is_ready", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(maic_bridge, "record_activity", lambda *_args, **_kwargs: None)
    launched = []
    monkeypatch.setattr(
        maic_bridge.subprocess,
        "Popen",
        lambda command, **_kwargs: launched.append(command) or SimpleNamespace(poll=lambda: None),
    )

    process, url = maic_bridge._start_maic_server_unlocked(fast_startup=True)
    assert process is not None
    assert url == "http://localhost:3100"
    assert launched[-1] == ["node", str(server)]
    assert len(launched) == 2
    assert len(gateway_checks) == 2
    assert (standalone_dir / ".next" / "static" / "chunks" / "app.js").is_file()
    assert (standalone_dir / "public" / "brand.svg").is_file()


def test_desktop_next_commands_bind_dev_server_to_loopback(tmp_path):
    command = maic_bridge._next_server_command(
        "node", tmp_path / "next", "dev", tmp_path / "server.js"
    )
    assert command == [
        "node",
        str(tmp_path / "next"),
        "dev",
        "--webpack",
        "--hostname",
        "127.0.0.1",
    ]


def test_desktop_standalone_command_uses_env_bound_entrypoint(tmp_path):
    command = maic_bridge._next_server_command(
        "node", tmp_path / "next", "start", tmp_path / "server.js"
    )
    assert command == ["node", str(tmp_path / "server.js")]
    env = {"HOSTNAME": "0.0.0.0"}
    maic_bridge._configure_next_server_environment(env)
    assert env["HOSTNAME"] == "127.0.0.1"
