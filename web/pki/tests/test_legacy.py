"""Synthetic legacy files only; no operator directories are inspected."""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
import unittest
import uuid

import test_pki as fixtures
from veilway_pki.core import MODES, PkiError, Store, load_json, mkdir, read_file, write_file, write_json
from veilway_pki.legacy import import_profiles, catalog


def legacy_fixture(root):
    seed = Store(root / "seed", fixtures.PkiTests.password)
    seed.import_ca(fixtures.PkiTests.source, fixtures.PkiTests.endpoints)
    bundle = root / "bundle"
    mkdir(bundle)
    entries, originals = [], {}
    for mode in MODES:
        pid = str(uuid.uuid4())
        seed.issue(pid, str(uuid.uuid4()), mode, fixtures.end())
        data = read_file(seed.current() / "profiles" / pid / "client.ovpn")
        filename = mode + ".ovpn"
        write_file(bundle / filename, data)
        entries.append(dict(file=filename, mode=mode, device_name="legacy-" + mode))
        originals[mode] = data
    write_json(bundle / "manifest.json", {"version": 1, "profiles": entries})
    target = Store(root / "target", fixtures.PkiTests.password)
    target.import_ca(seed.current(), fixtures.PkiTests.endpoints)
    return seed, target, bundle, originals


class LegacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PkiTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.PkiTests.tearDownClass()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="veilway-legacy-test-")
        self.seed, self.store, self.bundle, self.originals = legacy_fixture(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_original_bytes_all_modes_counters_replay_and_revoke(self):
        before = {name: read_file(self.store.current() / name) for name in ("ca/index.txt", "ca/serial", "ca/crlnumber", "crl.pem", "ca/private/ca.key")}
        identifiers = import_profiles(self.store, self.bundle)
        committed = self.store.current().name
        self.assertEqual(import_profiles(self.store, self.bundle), identifiers)
        self.assertEqual(self.store.current().name, committed)
        page = catalog(self.store, "", "")
        self.assertEqual(len(page["profiles"]), 3)
        for record in page["profiles"]:
            data = read_file(self.store.current() / "profiles" / record["profile_id"] / "client.ovpn")
            self.assertEqual(data, self.originals[record["mode"]])
            self.assertEqual(record["import_sha256"], hashlib.sha256(data).hexdigest())
        for name, data in before.items():
            self.assertEqual(read_file(self.store.current() / name), data)
        pid = identifiers[0]
        self.store.revoke(pid, str(uuid.uuid4()))
        with self.assertRaises(PkiError) as caught:
            self.store.download(pid)
        self.assertEqual(caught.exception.code, "revoked")
        self.assertEqual(import_profiles(self.store, self.bundle), identifiers)
        self.assertEqual(next(row for row in catalog(self.store, "", "")["profiles"] if row["profile_id"] == pid)["status"], "revoked")

    def test_invalid_bundle_is_atomic_and_does_not_execute_configuration(self):
        current = self.store.current().name
        filename = self.bundle / "aws-direct.ovpn"
        original = read_file(filename)
        variants = [original + b"up /private/should-never-execute\n", original + b"remote 192.0.2.99 1194\n",
                    original.replace(b"verify-x509-name aws-direct name", b"verify-x509-name yc-direct name"),
                    original.replace(b"<key>", b"<key>\n<key>"), original.replace(b"proto udp4", b"proto tcp-client")]
        for variant in variants:
            write_file(filename, variant)
            with self.assertRaises(PkiError):
                import_profiles(self.store, self.bundle)
            self.assertEqual(self.store.current().name, current)
        write_file(filename, original)
        self.assertEqual(len(import_profiles(self.store, self.bundle)), 3)

    def test_wrong_key_ca_wrapping_and_metadata_conflicts_are_rejected(self):
        filename = self.bundle / "aws-direct.ovpn"
        original = read_file(filename)
        other = self.originals["yc-direct"]
        def block(data, tag):
            return data.split(("<" + tag + ">\n").encode())[1].split(("</" + tag + ">").encode())[0]
        for tag in ("key", "tls-crypt-v2", "cert", "ca"):
            write_file(filename, original.replace(block(original, tag), block(other, "cert" if tag == "ca" else tag)))
            with self.assertRaises(PkiError):
                import_profiles(self.store, self.bundle)
        write_file(filename, original)
        import_profiles(self.store, self.bundle)
        current = self.store.current().name
        manifest = load_json(self.bundle / "manifest.json")
        manifest["profiles"][0]["device_name"] = "changed-import-name"
        write_json(self.bundle / "manifest.json", manifest)
        with self.assertRaises(PkiError) as caught:
            import_profiles(self.store, self.bundle)
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(self.store.current().name, current)

    def test_crash_after_commit_is_recoverable_and_snapshot_cursor_is_bound(self):
        def crash(point):
            if point == "after_commit":
                raise RuntimeError("synthetic failure")
        self.store.fault = crash
        with self.assertRaises(PkiError):
            import_profiles(self.store, self.bundle)
        self.store.fault = lambda point: None
        committed = self.store.current().name
        self.assertEqual(len(import_profiles(self.store, self.bundle)), 3)
        self.assertEqual(self.store.current().name, committed)
        with self.assertRaises(PkiError):
            catalog(self.store, "", str(uuid.uuid4()))

    def test_historical_revoked_and_expired_profiles_keep_status_and_restore(self):
        seed_path = self.seed.current()
        # Add an expired certificate to the synthetic old authority, not to target.
        self.seed.run(["/usr/bin/openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", "expired.key"], seed_path)
        self.seed.run(["/usr/bin/openssl", "req", "-new", "-key", "expired.key", "-subj", "/CN=expired", "-out", "expired.csr"], seed_path)
        self.seed.ca(seed_path, "-extensions", "client_cert", "-startdate", "20200101000000Z", "-enddate", "20200102000000Z",
                     "-in", "expired.csr", "-out", "expired.crt", "-notext")
        self.seed.ca(seed_path, "-gencrl", "-out", "crl.pem")
        target = Store(Path(self.temporary.name) / "historical", fixtures.PkiTests.password)
        target.import_ca(seed_path, fixtures.PkiTests.endpoints)
        manifest = load_json(self.bundle / "manifest.json")
        original = self.originals["yc-direct"]
        def replace_block(data, tag, contents):
            start = data.index(("<" + tag + ">\n").encode()) + len(tag) + 3
            finish = data.index(("</" + tag + ">").encode())
            return data[:start] + contents.strip() + b"\n" + data[finish:]
        for name in ("expired", "old-revoked"):
            source = seed_path if name == "expired" else fixtures.PkiTests.source
            data = replace_block(original, "cert", read_file(source / (name + ".crt")))
            data = replace_block(data, "key", read_file(source / (name + ".key")))
            write_file(self.bundle / (name + ".ovpn"), data)
            manifest["profiles"].append(dict(file=name + ".ovpn", mode="yc-direct", device_name=name))
        write_json(self.bundle / "manifest.json", manifest)
        import_profiles(target, self.bundle)
        historical = {row["device_name"]: row for row in catalog(target, "", "")["profiles"]}
        self.assertEqual(historical["expired"]["status"], "expired")
        self.assertEqual(historical["old-revoked"]["status"], "revoked")
        for name, code in (("expired", "expired"), ("old-revoked", "revoked")):
            with self.assertRaises(PkiError) as caught:
                target.download(historical[name]["profile_id"])
            self.assertEqual(caught.exception.code, code)
        backup = Path(self.temporary.name) / "restored"
        shutil.copytree(target.root, backup)
        restored = Store(backup, fixtures.PkiTests.password)
        self.assertEqual(catalog(restored, "", ""), catalog(target, "", ""))
        self.assertEqual(import_profiles(restored, self.bundle), import_profiles(target, self.bundle))

    def test_paths_permissions_duplicates_and_rpc_import_are_rejected(self):
        manifest = load_json(self.bundle / "manifest.json")
        manifest["profiles"].append(dict(manifest["profiles"][0]))
        write_json(self.bundle / "manifest.json", manifest)
        with self.assertRaises(PkiError):
            import_profiles(self.store, self.bundle)
        manifest["profiles"].pop()
        manifest["profiles"][0]["file"] = "../outside.ovpn"
        write_json(self.bundle / "manifest.json", manifest)
        with self.assertRaises(PkiError):
            import_profiles(self.store, self.bundle)
        with self.assertRaises(PkiError):
            self.store.dispatch({"operation": "import-profiles", "source": str(self.bundle)})
        self.bundle.chmod(0o755)
        with self.assertRaises(PkiError):
            import_profiles(self.store, self.bundle)

    def test_symlink_and_same_certificate_under_another_filename_are_rejected(self):
        filename = self.bundle / "aws-direct.ovpn"
        outside = Path(self.temporary.name) / "outside.ovpn"
        filename.rename(outside)
        filename.symlink_to(outside)
        with self.assertRaises(PkiError):
            import_profiles(self.store, self.bundle)
        filename.unlink()
        outside.rename(filename)
        write_file(self.bundle / "duplicate.ovpn", read_file(filename))
        manifest = load_json(self.bundle / "manifest.json")
        manifest["profiles"].append(dict(file="duplicate.ovpn", mode="aws-direct", device_name="duplicate"))
        write_json(self.bundle / "manifest.json", manifest)
        before = self.store.current().name
        with self.assertRaises(PkiError) as caught:
            import_profiles(self.store, self.bundle)
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(self.store.current().name, before)

    def test_catalog_paginates_without_private_material_or_missing_profiles(self):
        import_profiles(self.store, self.bundle)
        current = self.store.current()
        original = load_json(next((current / "profiles").iterdir()) / "record.json")
        # Synthetic records exercise page boundaries without signing 70 certificates.
        for _ in range(70):
            identifier = str(uuid.uuid4())
            folder = current / "profiles" / identifier
            mkdir(folder)
            record = dict(original)
            record["legacy"] = {**original["legacy"], "profile_id": identifier}
            write_json(folder / "record.json", record)
        page = catalog(self.store, "", "")
        self.assertEqual(len(page["profiles"]), 64)
        tail = catalog(self.store, page["next_after"], page["generation"])
        self.assertEqual(len(tail["profiles"]), 9)
        self.assertEqual(tail["next_after"], "")
        identifiers = [row["profile_id"] for row in page["profiles"] + tail["profiles"]]
        self.assertEqual(identifiers, sorted(set(identifiers)))
        self.assertNotIn("BEGIN", json.dumps(page))
