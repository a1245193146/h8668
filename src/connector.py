"""connector.py — Unified remote connection layer for V2 backup system.

Provides:
  - SSHPasswordConnector: Linux via SSH + password
  - WinRMConnector: Windows via pywinrm (NTLM)
  - WindowsConnector: WinRM with SSH fallback
  - create_connector(config) factory
"""

from __future__ import annotations

import base64
import importlib
import logging
import time
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ─── Protocol (interface) ────────────────────────────────────────────────────


@runtime_checkable
class RemoteConnector(Protocol):
    """Unified interface for all remote connection types."""

    def exec_command(self, cmd: str) -> tuple[int, str, str]:
        """Execute a command. Returns (exit_code, stdout, stderr)."""
        ...

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Upload a local file to remote path. Returns True on success."""
        ...

    def download_file(self, remote_path: str, local_path: str) -> bool:
        """Download a remote file to local path. Returns True on success."""
        ...

    def disk_usage(self, path: str) -> dict[str, Any]:
        """Return disk usage: {total_gb, free_gb, used_gb, path}. {} on error."""
        ...

    def close(self) -> None:
        """Close the connection."""
        ...


# ─── SSH Password Connector ──────────────────────────────────────────────────


class SSHPasswordConnector:
    """SSH connection using username + password (no key files)."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 22,
        timeout: int = 30,
        max_retries: int = 3,
        retry_delay: int = 30,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._client: Any | None = None
        self._sftp: Any | None = None

    def _connect(self) -> None:
        """Establish SSH connection with retries."""
        paramiko = importlib.import_module("paramiko")

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    timeout=self.timeout,
                    banner_timeout=self.timeout,
                    auth_timeout=self.timeout,
                    look_for_keys=False,
                    allow_agent=False,
                )
                self._client = client
                logger.debug("SSH connected to %s (attempt %d)", self.host, attempt)
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "SSH connect attempt %d/%d to %s failed: %s",
                    attempt,
                    self.max_retries,
                    self.host,
                    exc,
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)

        if last_error is not None:
            raise last_error
        raise ConnectionError(f"SSH connection to {self.host} failed")

    def _ensure_connected(self) -> None:
        if self._client is None:
            self._connect()

    def exec_command(self, cmd: str) -> tuple[int, str, str]:
        try:
            self._ensure_connected()
            if self._client is None:
                raise ConnectionError("SSH client is not connected")
            _stdin, stdout, stderr = self._client.exec_command(cmd, timeout=300)
            exit_code = stdout.channel.recv_exit_status()
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            return exit_code, out, err
        except Exception as exc:
            logger.error("SSH exec_command failed on %s: %s", self.host, exc)
            return -1, "", str(exc)

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        try:
            self._ensure_connected()
            if self._client is None:
                raise ConnectionError("SSH client is not connected")
            sftp = self._client.open_sftp()
            try:
                sftp.put(local_path, remote_path)
            finally:
                sftp.close()
            logger.debug("SSH upload: %s → %s:%s", local_path, self.host, remote_path)
            return True
        except Exception as exc:
            logger.error(
                "SSH upload failed %s → %s:%s: %s",
                local_path,
                self.host,
                remote_path,
                exc,
            )
            return False

    def download_file(self, remote_path: str, local_path: str) -> bool:
        try:
            self._ensure_connected()
            if self._client is None:
                raise ConnectionError("SSH client is not connected")
            sftp = self._client.open_sftp()
            try:
                sftp.get(remote_path, local_path)
            finally:
                sftp.close()
            logger.debug("SSH download: %s:%s → %s", self.host, remote_path, local_path)
            return True
        except Exception as exc:
            logger.error(
                "SSH download failed %s:%s → %s: %s",
                self.host,
                remote_path,
                local_path,
                exc,
            )
            return False

    def disk_usage(self, path: str = "/") -> dict[str, Any]:
        code, out, _err = self.exec_command(f"df -k {path} | tail -1")
        if code != 0:
            return {}
        try:
            parts = out.split()
            total_kb = int(parts[1])
            used_kb = int(parts[2])
            free_kb = int(parts[3])
            return {
                "path": path,
                "total_gb": round(total_kb / 1024 / 1024, 2),
                "used_gb": round(used_kb / 1024 / 1024, 2),
                "free_gb": round(free_kb / 1024 / 1024, 2),
            }
        except (IndexError, ValueError):
            return {}

    def close(self) -> None:
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                logger.debug("Ignoring SSH SFTP close failure", exc_info=True)
        if self._client:
            try:
                self._client.close()
            except Exception:
                logger.debug("Ignoring SSH client close failure", exc_info=True)
        self._client = None
        self._sftp = None


# ─── WinRM Connector ──────────────────────────────────────────────────────────


class WinRMConnector:
    """Windows Remote Management connector using pywinrm with NTLM auth."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        domain: str = "",
        port: int = 5985,
        timeout: int = 300,
    ) -> None:
        self.host = host
        self.domain = domain
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self._session: Any | None = None

    def _get_session(self) -> Any:
        if self._session is None:
            try:
                winrm = importlib.import_module("winrm")
            except ImportError as exc:
                raise ImportError(
                    "pywinrm is required for WinRMConnector. Install package 'pywinrm'."
                ) from exc

            full_user = f"{self.domain}\\{self.username}" if self.domain else self.username
            self._session = winrm.Session(
                f"http://{self.host}:{self.port}/wsman",
                auth=(full_user, self.password),
                transport="ntlm",
                operation_timeout_sec=self.timeout,
                read_timeout_sec=self.timeout + 10,
            )
        return self._session

    def exec_command(self, cmd: str) -> tuple[int, str, str]:
        try:
            session = self._get_session()
            result = session.run_ps(cmd)
            exit_code = result.status_code
            stdout = result.std_out.decode("utf-8", errors="replace")
            stderr = result.std_err.decode("utf-8", errors="replace")
            return exit_code, stdout, stderr
        except Exception as exc:
            logger.error("WinRM exec_command failed on %s: %s", self.host, exc)
            return -1, "", str(exc)

    @staticmethod
    def _ps_quote(value: str) -> str:
        """Quote a string for use as a single-quoted PowerShell literal."""
        return "'" + value.replace("'", "''") + "'"

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Upload file via Base64-encoded PowerShell (for files up to ~50MB)."""
        try:
            with open(local_path, "rb") as file:
                data = file.read()
            b64 = base64.b64encode(data).decode("ascii")
            remote_literal = self._ps_quote(remote_path)

            code, _, err = self.exec_command(
                f"[System.IO.File]::WriteAllBytes({remote_literal}, [byte[]]::new(0))"
            )
            if code != 0:
                logger.error("WinRM upload initialize failed: %s", err)
                return False

            chunk_size = 50_000
            for i in range(0, len(b64), chunk_size):
                chunk = b64[i : i + chunk_size]
                ps = (
                    f"$b = [System.Convert]::FromBase64String('{chunk}');"
                    f"$s = [System.IO.File]::Open({remote_literal}, [System.IO.FileMode]::Append);"
                    "$s.Write($b, 0, $b.Length); $s.Close()"
                )
                code, _, err = self.exec_command(ps)
                if code != 0:
                    logger.error("WinRM upload chunk failed: %s", err)
                    return False
            logger.debug("WinRM upload: %s → %s:%s", local_path, self.host, remote_path)
            return True
        except Exception as exc:
            logger.error(
                "WinRM upload failed %s → %s:%s: %s",
                local_path,
                self.host,
                remote_path,
                exc,
            )
            return False

    def download_file(self, remote_path: str, local_path: str) -> bool:
        """Download file via Base64-encoded PowerShell."""
        try:
            remote_literal = self._ps_quote(remote_path)
            ps = f"[System.Convert]::ToBase64String([System.IO.File]::ReadAllBytes({remote_literal}))"
            code, b64_out, err = self.exec_command(ps)
            if code != 0:
                logger.error("WinRM download failed: %s", err)
                return False
            data = base64.b64decode(b64_out.strip())
            with open(local_path, "wb") as file:
                file.write(data)
            logger.debug("WinRM download: %s:%s → %s", self.host, remote_path, local_path)
            return True
        except Exception as exc:
            logger.error(
                "WinRM download failed %s:%s → %s: %s",
                self.host,
                remote_path,
                local_path,
                exc,
            )
            return False

    def disk_usage(self, path: str = "C:") -> dict[str, Any]:
        drive = path.rstrip("\\").rstrip("/")
        if not drive.endswith(":"):
            drive = "C:"
        ps = (
            f"$d = Get-WmiObject Win32_LogicalDisk -Filter \"DeviceID='{drive}'\";"
            'Write-Output "$($d.Size) $($d.FreeSpace)"'
        )
        code, out, _ = self.exec_command(ps)
        if code != 0 or not out.strip():
            return {}
        try:
            parts = out.strip().split()
            total_bytes = int(parts[0])
            free_bytes = int(parts[1])
            return {
                "path": drive,
                "total_gb": round(total_bytes / 1024**3, 2),
                "free_gb": round(free_bytes / 1024**3, 2),
                "used_gb": round((total_bytes - free_bytes) / 1024**3, 2),
            }
        except (IndexError, ValueError):
            return {}

    def close(self) -> None:
        self._session = None


# ─── Windows Connector (WinRM + SSH fallback) ─────────────────────────────────


class WindowsConnector:
    """
    Composite connector for Windows: tries WinRM first, falls back to SSH.
    Once a protocol succeeds, it stays with that protocol for the session.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        domain: str = "",
        winrm_port: int = 5985,
        ssh_port: int = 22,
        timeout: int = 300,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.domain = domain
        self.winrm_port = winrm_port
        self.ssh_port = ssh_port
        self.timeout = timeout
        self._active: WinRMConnector | SSHPasswordConnector | None = None
        self._protocol = "unknown"

    def _try_winrm(self) -> bool:
        """Attempt WinRM connection. Returns True if successful."""
        conn = WinRMConnector(
            self.host,
            self.username,
            self.password,
            domain=self.domain,
            port=self.winrm_port,
            timeout=self.timeout,
        )
        code, out, err = conn.exec_command("echo winrm_ok")
        if code == 0 and "winrm_ok" in out:
            self._active = conn
            self._protocol = "winrm"
            logger.info("WindowsConnector: connected via WinRM to %s", self.host)
            return True
        logger.warning("WinRM failed for %s: %s — trying SSH fallback", self.host, err)
        conn.close()
        return False

    def _try_ssh(self) -> bool:
        """Attempt SSH connection. Returns True if successful."""
        full_user = f"{self.domain}\\{self.username}" if self.domain else self.username
        conn = SSHPasswordConnector(
            self.host,
            full_user,
            self.password,
            port=self.ssh_port,
            timeout=30,
            max_retries=2,
            retry_delay=5,
        )
        code, out, err = conn.exec_command("echo ssh_ok")
        if code == 0 and "ssh_ok" in out:
            self._active = conn
            self._protocol = "ssh"
            logger.info("WindowsConnector: connected via SSH fallback to %s", self.host)
            return True
        logger.error("SSH fallback also failed for %s: %s", self.host, err)
        conn.close()
        return False

    def _ensure_connected(self) -> None:
        if self._active is not None:
            return
        if not self._try_winrm() and not self._try_ssh():
            raise ConnectionError(f"Cannot connect to Windows host {self.host} via WinRM or SSH")

    def exec_command(self, cmd: str) -> tuple[int, str, str]:
        try:
            self._ensure_connected()
            if self._active is None:
                raise ConnectionError("WindowsConnector has no active connection")
            return self._active.exec_command(cmd)
        except Exception as exc:
            logger.error("WindowsConnector exec_command failed on %s: %s", self.host, exc)
            return -1, "", str(exc)

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        try:
            self._ensure_connected()
            if self._active is None:
                raise ConnectionError("WindowsConnector has no active connection")
            return self._active.upload_file(local_path, remote_path)
        except Exception as exc:
            logger.error("WindowsConnector upload failed: %s", exc)
            return False

    def download_file(self, remote_path: str, local_path: str) -> bool:
        try:
            self._ensure_connected()
            if self._active is None:
                raise ConnectionError("WindowsConnector has no active connection")
            return self._active.download_file(remote_path, local_path)
        except Exception as exc:
            logger.error("WindowsConnector download failed: %s", exc)
            return False

    def disk_usage(self, path: str = "C:") -> dict[str, Any]:
        try:
            self._ensure_connected()
            if self._active is None:
                raise ConnectionError("WindowsConnector has no active connection")
            return self._active.disk_usage(path)
        except Exception as exc:
            logger.error("WindowsConnector disk_usage failed: %s", exc)
            return {}

    @property
    def active_protocol(self) -> str:
        return self._protocol

    def close(self) -> None:
        if self._active:
            try:
                self._active.close()
            except Exception:
                logger.debug("Ignoring WindowsConnector active close failure", exc_info=True)
        self._active = None
        self._protocol = "unknown"


# ─── Factory ──────────────────────────────────────────────────────────────────


def create_connector(config: dict[str, Any]) -> SSHPasswordConnector | WindowsConnector:
    """
    Create the appropriate connector based on config['os'].

    Linux config keys: host, port, username, password
    Windows config keys: host, winrm_port, ssh_port, domain, username, password
    """
    os_type = str(config.get("os", "linux")).lower()

    if os_type == "linux":
        return SSHPasswordConnector(
            host=str(config["host"]),
            username=str(config["username"]),
            password=str(config["password"]),
            port=int(config.get("port", 22)),
            timeout=int(config.get("timeout", 30)),
            max_retries=int(config.get("max_retries", 3)),
            retry_delay=int(config.get("retry_delay", 30)),
        )
    if os_type == "windows":
        return WindowsConnector(
            host=str(config["host"]),
            username=str(config["username"]),
            password=str(config["password"]),
            domain=str(config.get("domain", "")),
            winrm_port=int(config.get("winrm_port", 5985)),
            ssh_port=int(config.get("ssh_port", 22)),
            timeout=int(config.get("timeout", 300)),
        )
    raise ValueError(f"Unknown OS type: {os_type!r}. Must be 'linux' or 'windows'.")
