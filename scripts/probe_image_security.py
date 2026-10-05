import ctypes
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import sqlite3
import ssl
import stat
import subprocess
import sysconfig
import zlib
from pathlib import Path

importlib.import_module("paperwrench.main")

packages = subprocess.check_output(
    [
        "dpkg-query",
        "-W",
        "-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n",
    ],
    text=True,
)
files = {}
for line in packages.splitlines():
    name = line.split("\t")[0]
    files[name] = subprocess.check_output(["dpkg-query", "-L", name], text=True).splitlines()
privileged = []
for directory, _, names in os.walk("/usr"):
    for name in names:
        path = Path(directory) / name
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        if stat.S_ISREG(mode) and mode & 0o6000:
            privileged.append({"path": str(path), "mode": oct(mode & 0o7777)})
perl = {}
for module in ("Pod::Text", "Archive::Tar", "File::Temp"):
    r = subprocess.run(["perl", "-M" + module, "-e", "1"], capture_output=True, text=True)
    perl[module] = {"importable": r.returncode == 0, "stderr": r.stderr}
conn = sqlite3.connect(":memory:")
opts = [r[0] for r in conn.execute("pragma compile_options")]
lib = ctypes.CDLL("libsqlite3.so.0")
symbols = {
    n: hasattr(lib, n)
    for n in (
        "sqlite3changeset_apply_v3",
        "sqlite3changeset_concat",
        "sqlite3changegroup_add",
        "sqlite3_zipfile_init",
    )
}
try:
    conn.execute("select zipfile('nonexistent')")
    zip_error = None
except sqlite3.Error as exc:
    zip_error = str(exc)
result = {
    "uid": int(subprocess.check_output(["id", "-u"], text=True)),
    "architecture": platform.machine(),
    "debian": platform.freedesktop_os_release(),
    "debian_version": Path("/etc/debian_version").read_text().strip(),
    "python": platform.python_version(),
    "libc": platform.libc_ver(),
    "sqlite": sqlite3.sqlite_version,
    "openssl": ssl.OPENSSL_VERSION,
    "zlib": zlib.ZLIB_RUNTIME_VERSION,
    "packages_tsv": packages,
    "package_files": files,
    "privileged_files": privileged,
    "perl_modules": perl,
    "perl_ivsize": subprocess.check_output(["perl", "-V:ivsize"], text=True).strip(),
    "fstab": Path("/etc/fstab").read_text(),
    "subuid": Path("/etc/subuid").read_text(),
    "subgid": Path("/etc/subgid").read_text(),
    "login_defs": Path("/etc/login.defs").read_text(),
    "tools": {
        n: shutil.which(n)
        for n in (
            "infocmp",
            "tic",
            "getfacl",
            "setfacl",
            "chacl",
            "getfattr",
            "setfattr",
            "bzip2recover",
            "nscd",
            "systemd",
            "systemd-homed",
            "systemd-oomd",
            "systemd-journald",
            "nsenter",
            "mount",
            "newuidmap",
            "apt-key",
        )
    },
    "sqlite_compile_options": opts,
    "sqlite_symbols": symbols,
    "sqlite_zipfile_probe_error": zip_error,
    "installer_imports": {
        n: importlib.util.find_spec(n) is not None
        for n in ("pip", "setuptools", "wheel", "ensurepip")
    },
    "python_packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
    "loaded_libraries": sorted(
        {
            line.split()[-1]
            for line in Path("/proc/self/maps").read_text().splitlines()
            if len(line.split()) > 5 and ".so" in line.split()[-1]
        }
    ),
    "stdlib_files": {
        n: str(Path(sysconfig.get_path("stdlib")) / (n + ".py"))
        for n in ("base64", "imaplib", "poplib", "tempfile", "pkgutil")
    },
}
result["usrmerge"] = {name: str(Path(name).resolve()) for name in ("/bin", "/sbin")}
result["node_modules"] = [
    str(Path(root) / "node_modules")
    for base in ("/usr", "/app", "/opt")
    for root, dirs, _ in os.walk(base)
    if "node_modules" in dirs
]
print(json.dumps(result, sort_keys=True, indent=2))
