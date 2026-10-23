"""Private creation, without changing existing files or global process umasks.

The directory must belong to the host application. These checks are not a
sandbox against a process that can replace directory entries concurrently.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path


def _windows_create(path: Path, *, directory: bool) -> bool:
    """Create with a protected current-user/SYSTEM DACL, using Windows APIs.

    Applying the descriptor at creation avoids an initially public file. This
    does not change existing ACLs or restrict administrators/backup operators.
    """
    import ctypes
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("length", w.DWORD), ("descriptor", w.LPVOID), ("inherit", w.BOOL)]

    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    kernel.LocalFree.argtypes = [w.HLOCAL]
    kernel.LocalFree.restype = w.HLOCAL
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    advapi.OpenProcessToken.restype = w.BOOL
    advapi.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)]
    advapi.GetTokenInformation.restype = w.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [w.LPVOID, ctypes.POINTER(w.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = w.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD,
                                                                          ctypes.POINTER(w.LPVOID), w.LPVOID]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    token, length, sid_text, descriptor = w.HANDLE(), w.DWORD(), w.LPWSTR(), w.LPVOID()
    try:
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))  # TokenUser
        if not length.value:
            raise ctypes.WinError(ctypes.get_last_error())
        user = ctypes.create_string_buffer(length.value)
        if not advapi.GetTokenInformation(token, 1, user, length, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(user, ctypes.POINTER(w.LPVOID))[0]  # TOKEN_USER starts with SID_AND_ATTRIBUTES.
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise ctypes.WinError(ctypes.get_last_error())
        inheritance = "OICI" if directory else ""
        sddl = f"D:P(A;{inheritance};FA;;;{sid_text.value})(A;{inheritance};FA;;;SY)"
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        if directory:
            kernel.CreateDirectoryW.argtypes = [w.LPCWSTR, ctypes.POINTER(SecurityAttributes)]
            kernel.CreateDirectoryW.restype = w.BOOL
            success = kernel.CreateDirectoryW(str(path), ctypes.byref(attributes))
            if success:
                return True
        else:
            kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.POINTER(SecurityAttributes),
                                           w.DWORD, w.DWORD, w.HANDLE]
            kernel.CreateFileW.restype = w.HANDLE
            handle = kernel.CreateFileW(str(path), 0xC0000000, 7, ctypes.byref(attributes), 1, 0x80, None)
            if handle != ctypes.c_void_p(-1).value:
                kernel.CloseHandle(handle)
                return True
        error = ctypes.get_last_error()
        if error in (80, 183):  # ERROR_FILE_EXISTS / ERROR_ALREADY_EXISTS
            return False
        raise ctypes.WinError(error)
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, w.HLOCAL))
        if token:
            kernel.CloseHandle(token)


def private_directory(path: Path) -> None:
    """Create missing directories privately; leave existing permissions alone."""
    path = Path(path)
    if path.is_dir():
        return
    if path.parent != path:
        private_directory(path.parent)
    if os.name == "nt":
        _windows_create(path, directory=True)
    else:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    if not path.is_dir():
        raise NotADirectoryError(path)


def database_paths(path: Path) -> tuple[Path, ...]:
    return path, Path(str(path) + "-wal"), Path(str(path) + "-shm"), Path(str(path) + "-journal")


def regular_database_paths(path: Path) -> None:
    for item in database_paths(path):
        try:
            mode = item.lstat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(mode):
            raise ValueError("Database and journal paths must be regular files, not symlinks or devices.")


def private_database(path: str) -> str:
    """Return creation protection applied, never silently chmod an existing DB."""
    if path == ":memory:":
        return "memory"
    if not path or "\x00" in path:
        raise ValueError("Database path must be nonempty and contain no NUL characters.")
    selected = Path(path)
    regular_database_paths(selected)
    if os.name == "nt":
        created = _windows_create(selected, directory=False)
        protection = "windows_owner_system_dacl"
    else:
        try:
            descriptor = os.open(selected, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            created = False
        else:
            os.close(descriptor)
            created = True
        protection = "posix_owner_only"
    regular_database_paths(selected)
    return protection if created else "existing_permissions_unchanged"


def permission_status(path: str) -> dict:
    """Content-free observations, not filesystem encryption or ACL attestation."""
    if path == ":memory:":
        return {"mechanism": "not_applicable", "current_access_verified": False, "files": []}
    files = []
    for kind, selected in zip(("database", "wal", "shm", "journal"), database_paths(Path(path))):
        try:
            observed = selected.lstat()
        except FileNotFoundError:
            files.append({"kind": kind, "present": False})
            continue
        record = {"kind": kind, "present": True, "regular": stat.S_ISREG(observed.st_mode)}
        if os.name != "nt":
            record.update(mode=oct(stat.S_IMODE(observed.st_mode)),
                          owner_only_mode=(observed.st_mode & 0o077) == 0,
                          owned_by_current_user=observed.st_uid == os.geteuid())
        files.append(record)
    return {"mechanism": "windows_acl" if os.name == "nt" else "posix_mode_bits",
            "current_access_verified": False,
            "note": "ACLs, parent-directory access, privileged users and storage encryption are not verified.",
            "files": files}
