import os
import re
import selectors
import signal
import subprocess
import time

from tunnelui.agent.files import SecureManagedFiles
from tunnelui.agent.registry import ManagedInstance

USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")
MAX_EXPORT_BYTES = 64 * 1024


class ExportError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class TrustTunnelCliExporter:
    def __init__(self, files: SecureManagedFiles):
        self.files = files
        self._verified: set[tuple[str, int, int, int, int]] = set()

    def export(self, instance: ManagedInstance, username: str, format: str) -> bytes:
        if not USERNAME.fullmatch(username) or format not in {"deeplink", "toml"}:
            raise ExportError("invalid_export_request")
        arguments = [
            instance.files["vpn"].name,
            instance.files["hosts"].name,
            "-c", username,
            "-a", instance.public_address,
            "--format", format,
        ]
        with self.files.open_binary(instance) as (descriptor, directory_descriptor, info):
            self._verify_capability(instance, descriptor, directory_descriptor, info)
            return_code, output = self._run(
                descriptor, directory_descriptor, arguments, 10.0
            )
        output = output.strip()
        if return_code or not output:
            raise ExportError("profile_export_failed")
        if format == "deeplink":
            output = output.splitlines()[0].strip()
            if not output.startswith(b"tt://"):
                raise ExportError("profile_export_invalid")
        return output

    def _verify_capability(
        self, instance: ManagedInstance, descriptor: int, directory_descriptor: int, info
    ) -> None:
        key = (instance.id, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)
        if key in self._verified:
            return
        version_code, version_text = self._run(
            descriptor, directory_descriptor, ["--version"], 3.0
        )
        help_code, help_text = self._run(
            descriptor, directory_descriptor, ["--help"], 3.0
        )
        required = (b"-c", b"-a", b"--format", b"deeplink", b"toml")
        if (
            version_code
            or instance.expected_version.encode() not in version_text
            or help_code
            or any(token not in help_text for token in required)
        ):
            raise ExportError("unsupported_trusttunnel_cli")
        self._verified.add(key)

    @staticmethod
    def _run(
        descriptor: int, directory_descriptor: int, arguments: list[str], timeout: float
    ) -> tuple[int, bytes]:
        executable = f"/proc/self/fd/{descriptor}"
        process = None
        try:
            process = subprocess.Popen(
                [executable, *arguments],
                cwd=f"/proc/self/fd/{directory_descriptor}",
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                pass_fds=(descriptor, directory_descriptor),
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                start_new_session=True,
            )
            assert process.stdout is not None
            output = bytearray()
            deadline = time.monotonic() + timeout
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ExportError("profile_export_timeout")
                    if not selector.select(remaining):
                        raise ExportError("profile_export_timeout")
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        selector.unregister(process.stdout)
                        break
                    output.extend(chunk)
                    if len(output) > MAX_EXPORT_BYTES:
                        raise ExportError("profile_export_too_large")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExportError("profile_export_timeout")
            return process.wait(timeout=remaining), bytes(output)
        except subprocess.TimeoutExpired:
            raise ExportError("profile_export_timeout") from None
        except OSError:
            raise ExportError("profile_export_failed") from None
        finally:
            if process is not None:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
                if process.stdout is not None:
                    process.stdout.close()

