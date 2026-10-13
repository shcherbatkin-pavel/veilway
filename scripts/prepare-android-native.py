#!/usr/bin/env python3
"""Explicitly fetch checksum-pinned sources and cross-compile OpenSSL locally."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "android/third-party/sources"
NDK_VERSION = "27.2.12479018"


def run(args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Explicitly permit public source downloads")
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--abis", nargs="+", choices=("arm64-v8a", "x86_64"), default=["arm64-v8a", "x86_64"])
    args = parser.parse_args()
    SOURCES.mkdir(parents=True, exist_ok=True)
    for dep in json.loads((ROOT / "android/third-party/dependencies.json").read_text()):
        archive = SOURCES / (dep["name"] + ".tar.gz")
        if not archive.exists():
            if not args.download:
                parser.error("Missing sources; use --download to allow the documented downloads")
            print("Downloading", dep["name"], dep["version"], flush=True)
            temporary = archive.with_suffix(".tmp")
            run(["curl", "--fail", "--location", "--silent", "--show-error", "--max-time", "300", "--output", str(temporary), dep["url"]])
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != dep["sha256"]:
                raise SystemExit("Source checksum mismatch: " + dep["name"])
            temporary.replace(archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != dep["sha256"]:
            raise SystemExit("Source checksum mismatch: " + dep["name"])
        target = SOURCES / dep["name"]
        marker = target / ".veilway-source-sha256"
        if target.exists() and (not marker.exists() or marker.read_text() != dep["sha256"]):
            raise SystemExit("Source version changed: move the ignored source/build directories aside, then prepare again")
        if not target.exists():
            with tarfile.open(archive) as tar:
                tar.extractall(SOURCES, filter="data")
            (SOURCES / dep["root"]).rename(target)
            marker.write_text(dep["sha256"])
    ndk = args.sdk_root.resolve() / "ndk" / NDK_VERSION
    toolchain = ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin"
    if not toolchain.is_dir():
        parser.error("Install the pinned Linux NDK in SDK root first")
    for abi in args.abis:
        prefix = SOURCES / ("openssl-" + abi)
        identity = (SOURCES / "openssl/.veilway-source-sha256").read_text() + ":" + NDK_VERSION + ":api29"
        build_marker = prefix / ".veilway-build-id"
        if (prefix / "lib/libssl.a").exists():
            if not build_marker.exists() or build_marker.read_text() != identity:
                raise SystemExit("OpenSSL build version changed: move its ignored build/install directories aside before rebuilding")
            continue
        work = SOURCES / ("openssl-build-" + abi)
        if not work.exists():
            shutil.copytree(SOURCES / "openssl", work)
        env = dict(os.environ, ANDROID_NDK_ROOT=str(ndk), PATH=str(toolchain) + os.pathsep + os.environ["PATH"])
        target = "android-arm64" if abi == "arm64-v8a" else "android-x86_64"
        print("Building OpenSSL for", abi, flush=True)
        run(["perl", "Configure", target, "-D__ANDROID_API__=29", "no-shared", "no-tests", "no-apps", "no-docs", "no-autoload-config",
             "-fPIC", "--prefix=" + str(prefix), "--libdir=lib"], cwd=work, env=env)
        with (work / "build.log").open("w") as log:
            run(["make", "-j4"], cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
            run(["make", "install_sw"], cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
        build_marker.write_text(identity)
    print("Native sources and OpenSSL ready.")


if __name__ == "__main__":
    main()
