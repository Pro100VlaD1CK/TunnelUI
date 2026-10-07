import multiprocessing
import os
import socket
import stat
import subprocess
import uuid
from pathlib import Path

import pytest

from tunnelui.agent.exporter import ExportError, TrustTunnelCliExporter
from tunnelui.agent.files import FileSecurityError, SecureManagedFiles
from tunnelui.agent.locking import LockError, ProcessLocks
from tunnelui.agent.registry import ManagedInstance

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux/POSIX security boundary")


def make_instance(tmp_path: Path) -> ManagedInstance:
    tmp_path.mkdir(parents=True, exist_ok=True)
    work = tmp_path / "work"
    backup = tmp_path / "backups"
    work.mkdir(mode=0o755)
    backup.mkdir(mode=0o700)
    files = {key: work / f"{key}.toml" for key in ("vpn", "hosts", "credentials", "rules")}
    for key, path in files.items():
        path.write_bytes(f"{key}\n".encode())
        path.chmod(0o600 if key == "credentials" else 0o644)
    binary = work / "trusttunnel_endpoint"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o700)
    return ManagedInstance(
        id="primary", service="trusttunnel.service", working_directory=work,
        binary=binary, files=files, backup_root=backup,
        public_address="vpn.example.invalid:443", expected_version="1.1.0",
        health_host="127.0.0.1", health_port=443,
        tls_server_name="vpn.example.invalid", quic_configured=True,
        allow_reload=False, owner_uid=os.getuid(),
    )


def test_secure_atomic_backup_restore_and_fsync(tmp_path, monkeypatch):
    instance = make_instance(tmp_path)
    files = SecureManagedFiles()
    calls = []
    original_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda descriptor: (calls.append(descriptor), original_fsync(descriptor))[1])
    operation_id = str(uuid.uuid4())
    assert files.snapshot(instance)["credentials"] == b"credentials\n"
    files.create_backup(instance, operation_id)
    files.prepare_credentials(instance, operation_id, b"new credentials\n")
    files.commit_credentials(instance, operation_id)
    assert instance.files["credentials"].read_bytes() == b"new credentials\n"
    files.restore_credentials(instance, operation_id)
    assert instance.files["credentials"].read_bytes() == b"credentials\n"
    assert len(calls) >= 8


def test_restore_after_crash_with_prepared_credentials(tmp_path):
    instance = make_instance(tmp_path)
    files = SecureManagedFiles()
    operation_id = str(uuid.uuid4())
    files.create_backup(instance, operation_id)
    files.prepare_credentials(instance, operation_id, b"unfinished replacement\n")

    files.restore_credentials(instance, operation_id)

    assert instance.files["credentials"].read_bytes() == b"credentials\n"
    assert not (instance.working_directory / f".tunnelui-{operation_id}-credentials.tmp").exists()


def test_secure_files_reject_symlink_hardlink_owner_and_mode(tmp_path):
    files = SecureManagedFiles()
    symlinked = make_instance(tmp_path / "symlink")
    symlinked.files["rules"].unlink()
    symlinked.files["rules"].symlink_to(symlinked.files["vpn"])
    with pytest.raises(FileSecurityError):
        files.snapshot(symlinked)

    hardlinked = make_instance(tmp_path / "hardlink")
    hardlinked.files["rules"].unlink()
    os.link(hardlinked.files["vpn"], hardlinked.files["rules"])
    with pytest.raises(FileSecurityError, match="unsafe_managed_file"):
        files.snapshot(hardlinked)

    unsafe_mode = make_instance(tmp_path / "mode")
    unsafe_mode.files["vpn"].chmod(0o666)
    with pytest.raises(FileSecurityError, match="unsafe_file_mode"):
        files.snapshot(unsafe_mode)

    wrong_owner = make_instance(tmp_path / "owner")
    object.__setattr__(wrong_owner, "owner_uid", os.getuid() + 1)
    with pytest.raises(FileSecurityError, match="unsafe_managed_directory"):
        files.snapshot(wrong_owner)

    real = make_instance(tmp_path / "ancestor")
    alias = tmp_path / "work-alias"
    alias.symlink_to(real.working_directory, target_is_directory=True)
    object.__setattr__(real, "working_directory", alias)
    object.__setattr__(real, "binary", alias / real.binary.name)
    object.__setattr__(
        real, "files", {key: alias / path.name for key, path in real.files.items()}
    )
    with pytest.raises(FileSecurityError, match="unsafe_managed_directory"):
        files.snapshot(real)


def _attempt_lock(root: str, instance: ManagedInstance, operation_id: str, queue):
    try:
        ProcessLocks(Path(root)).acquire(instance, operation_id, os.getpid())
    except LockError as error:
        queue.put(error.code)
    else:
        queue.put("acquired")


def test_process_lock_conflicts_across_processes(tmp_path):
    instance = make_instance(tmp_path)
    lock_root = tmp_path / "locks"
    lock_root.mkdir(mode=0o700)
    locks = ProcessLocks(lock_root)
    held_id = str(uuid.uuid4())
    locks.acquire(instance, held_id, os.getpid())
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    process = context.Process(
        target=_attempt_lock,
        args=(str(lock_root), instance, str(uuid.uuid4()), queue),
    )
    process.start()
    process.join(5)
    assert process.exitcode == 0
    assert queue.get(timeout=1) == "operation_in_progress"
    locks.release(instance, held_id, os.getpid())
    locks.release(instance, held_id, os.getpid())


def test_stale_peer_reservation_can_be_reclaimed(tmp_path, monkeypatch):
    instance = make_instance(tmp_path)
    root = tmp_path / "locks"
    root.mkdir(mode=0o700)
    locks = ProcessLocks(root)
    locks.acquire(instance, str(uuid.uuid4()), 999999)
    monkeypatch.setattr("tunnelui.agent.locking._pid_alive", lambda _pid: False)
    replacement = str(uuid.uuid4())
    locks.acquire(instance, replacement, os.getpid())
    locks.require(instance, replacement, os.getpid())
    locks.release(instance, replacement, os.getpid())


def test_lock_file_symlink_is_rejected(tmp_path):
    instance = make_instance(tmp_path)
    root = tmp_path / "locks"
    root.mkdir(mode=0o700)
    (root / "primary.lock").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(LockError, match="lock_failed"):
        ProcessLocks(root).acquire(instance, str(uuid.uuid4()), os.getpid())


def test_unix_socket_permission_and_peer_credentials(tmp_path):
    address = tmp_path / "agent.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(address))
        os.chmod(address, 0o660)
        server.listen(1)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(address))
            accepted, _ = server.accept()
            with accepted:
                assert stat.S_IMODE(address.stat().st_mode) == 0o660
                assert accepted.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)


def test_official_cli_export_contract_uses_verified_descriptor(tmp_path):
    managed = make_instance(tmp_path)
    managed.binary.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 1.1.0; exit 0; fi\n"
        "if [ \"$1\" = \"--help\" ]; then echo '-c -a --format deeplink toml'; exit 0; fi\n"
        "if [ \"$8\" = \"deeplink\" ]; then echo 'tt://?sandbox-secret'; else echo 'server = test'; fi\n",
        encoding="utf-8",
    )
    managed.binary.chmod(0o700)
    exporter = TrustTunnelCliExporter(SecureManagedFiles())
    assert exporter.export(managed, "alice", "deeplink") == b"tt://?sandbox-secret"
    assert exporter.export(managed, "alice", "toml") == b"server = test"


def test_export_timeout_is_redacted(tmp_path, monkeypatch):
    managed = make_instance(tmp_path)
    exporter = TrustTunnelCliExporter(SecureManagedFiles())

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["secret", "argv"], 1, output=b"secret")

    monkeypatch.setattr(subprocess, "Popen", timeout)
    with pytest.raises(Exception, match="profile_export_timeout") as captured:
        exporter.export(managed, "alice", "deeplink")
    assert "secret" not in str(captured.value)


def test_export_rejects_stdout_beyond_limit(tmp_path):
    managed = make_instance(tmp_path)
    managed.binary.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 1.1.0; exit 0; fi\n"
        "if [ \"$1\" = \"--help\" ]; then echo '-c -a --format deeplink toml'; exit 0; fi\n"
        "head -c 70000 /dev/zero\n",
        encoding="utf-8",
    )
    managed.binary.chmod(0o700)
    with pytest.raises(ExportError, match="profile_export_too_large"):
        TrustTunnelCliExporter(SecureManagedFiles()).export(managed, "alice", "deeplink")

