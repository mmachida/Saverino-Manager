from __future__ import annotations

import os
from pathlib import Path


class InstanceLock:
    """Lock exclusivo simples para impedir duas instâncias sobre o mesmo catálogo."""

    # Initialize the object.
    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: int | None = None

    # Acquire.
    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self._fd, str(os.getpid()).encode())
            return True
        except FileExistsError:
            try:
                pid = int(self.path.read_text(encoding="ascii"))
                if self._pid_is_alive(pid):
                    return False
            except (OSError, ValueError):
                pass
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            return self.acquire()

    # Check process liveness safely.
    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.CloseHandle.restype = wintypes.BOOL
            handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not handle:
                return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER
            try:
                return kernel.WaitForSingleObject(handle, 0) != 0  # WAIT_OBJECT_0 means exited
            finally:
                kernel.CloseHandle(handle)

        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    # Handle release.
    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd); self._fd = None
        try: self.path.unlink()
        except FileNotFoundError: pass

    # Enter the context.
    def __enter__(self):
        if not self.acquire(): raise RuntimeError("O catálogo já está aberto em outra instância")
        return self

    # Exit the context.
    def __exit__(self, *_): self.release()
