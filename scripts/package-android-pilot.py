#!/usr/bin/env python3
"""Package a verified APK and corresponding source bundle without local secrets."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-root", type=Path, required=True)
    args = parser.parse_args()
    apk = ROOT / "android/app/build/outputs/apk/release/app-release.apk"
    subprocess.run([str(args.sdk_root / "build-tools/36.0.0/apksigner"), "verify", str(apk)], check=True)
    metadata = json.loads((apk.parent / "output-metadata.json").read_text())
    version = metadata["elements"][0]["versionName"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-pilot", version):
        raise SystemExit("Invalid release version")
    output = ROOT / "release-artifacts/android" / version
    output.mkdir(parents=True, exist_ok=True)
    released = output / f"veilway-{version}.apk"
    shutil.copyfile(apk, released)
    source = output / f"veilway-{version}-sources.tar.gz"
    # Explicit allowlist: no build trees, operator files, profiles or signing keys.
    directories = ["android/app/src", "android/gradle"]
    files = ["LICENSE", "android/settings.gradle.kts", "android/build.gradle.kts", "android/gradle.properties", "android/gradlew", "android/gradlew.bat",
        "android/app/build.gradle.kts", "android/app/gradle.lockfile", "android/third-party/dependencies.json", "android/third-party/NOTICE.md",
        "scripts/prepare-android-native.py", "scripts/android-pilot-key.py", "scripts/package-android-pilot.py", "scripts/test-android-tunnel.py",
        "docs/android-client.md", "web/backend/tests/fixtures/client-contract.properties"]
    dependencies = json.loads((ROOT / "android/third-party/dependencies.json").read_text())
    for dep in dependencies:
        archive = ROOT / "android/third-party/sources" / (dep["name"] + ".tar.gz")
        if hashlib.sha256(archive.read_bytes()).hexdigest() != dep["sha256"]:
            raise SystemExit("Source checksum mismatch")
        files.append(str(archive.relative_to(ROOT)))
    with tarfile.open(source, "w:gz") as tar:
        for name in directories + files:
            tar.add(ROOT / name, arcname=name)
    checksums = output / "SHA256SUMS"
    checksums.write_text("".join(hashlib.sha256(file.read_bytes()).hexdigest() + "  " + file.name + "\n" for file in (released, source)))
    instructions = (ROOT / "docs/android-pilot-user.md").read_text()
    (output / "INSTALL.md").write_text(
        f"# Veilway {version} для Android\n\n"
        f"Установите `veilway-{version}.apk` поверх существующего приложения, без удаления.\n\n"
        + instructions[instructions.index("1. Установите APK."):]
    )
    print("Verified pilot APK, matching sources, installation guide and SHA256SUMS:", output)


if __name__ == "__main__":
    main()
