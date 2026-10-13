#!/usr/bin/env python3
"""Create a local pilot signing key once. Never prints signing passwords."""
from pathlib import Path
import os
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.umask(0o077)
    directory = ROOT / "private/android"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = directory / "pilot.jks"
    properties = directory / "signing.properties"
    password_file = directory / "store-password"
    if any(p.exists() for p in (key, properties, password_file)):
        if all(p.is_file() for p in (key, properties, password_file)):
            print("Existing pilot key retained.")
            return
        raise SystemExit("Incomplete signing setup; recover it manually without overwriting the key.")
    password = secrets.token_urlsafe(48)
    password_file.write_text(password)
    subprocess.run(["keytool", "-genkeypair", "-keystore", str(key), "-storetype", "PKCS12",
        "-storepass:file", str(password_file), "-keypass:file", str(password_file),
        "-alias", "veilway-pilot", "-keyalg", "RSA", "-keysize", "3072", "-validity", "3650",
        "-dname", "CN=Veilway Pilot, O=Veilway", "-noprompt"], check=True)
    properties.write_text("storeFile=" + str(key) + "\nstorePassword=" + password + "\n")
    print("Pilot signing material created in private/android; back it up privately for future updates.")


if __name__ == "__main__":
    main()
