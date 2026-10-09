#!/usr/bin/env python3
"""Offline rollout contracts with fake Docker and synthetic public inputs."""
from contextlib import contextmanager
import hashlib
import os
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


prepare = module('prepare-api-web-release')
inspect = module('inspect-api-web-release')
IMAGE = 'sha256:' + 'a' * 64
NEW_IMAGE = 'sha256:' + 'b' * 64


def candidate_output(*arguments):
    return 'linux/amd64' if arguments[3] == '{{.Os}}/{{.Architecture}}' else NEW_IMAGE


@contextmanager
def restrictive_umask():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


class ReleaseTests(unittest.TestCase):
    def test_compatibility_rejects_migrations_dependencies_manifest_and_configuration(self):
        for name in ('backend/migrations/versions/new.py', 'backend/pyproject.toml', 'backend/Dockerfile',
                     'backend/container-entrypoint.py', 'frontend/package-lock.json', 'frontend/Caddyfile', 'compose.yaml'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                prepare.validate_compatibility({name: b'old'}, {name: b'new'})
        prepare.validate_compatibility({'backend/src/app.py': b'old'}, {'backend/src/app.py': b'new', 'frontend/src/new.ts': b'new'})

    def test_default_preparation_does_not_write_or_call_docker(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(prepare, 'source_files', return_value={'compose.yaml': b'test'}), patch.object(prepare.subprocess, 'run') as run:
            target = Path(directory) / 'release'
            prepare.prepare('a' * 40, 'b' * 40, target, build=False)
            self.assertFalse(target.exists())
            run.assert_not_called()

    def test_export_rejects_symlinks(self):
        with patch.object(prepare, 'git', return_value=b'120000 blob abc\tweb/backend/src/link\0'):
            with self.assertRaises(ValueError):
                prepare.source_files('a' * 40)

    def fixture(self, directory):
        root = Path(directory)
        release, app = root / 'release', root / 'app'
        release.mkdir(mode=0o700)
        app.mkdir()
        baseline = {'compose.yaml': b'synthetic compose', 'backend/src/app.py': b'synthetic code'}
        for name, value in baseline.items():
            path = app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        (release / 'images.tar').write_bytes(b'synthetic archive')
        (release / 'inspect-api-web-release.py').write_bytes(b'synthetic checker')
        images = {service: {'tag': 'veilway-control-' + service + ':' + 'c' * 12, 'id': NEW_IMAGE} for service in ('api', 'web')}
        manifest = {'version': 1, 'images': images,
                    'baseline_files': {name: hashlib.sha256(value).hexdigest() for name, value in baseline.items()},
                    'archive_sha256': inspect.digest(release / 'images.tar'),
                    'checker_sha256': inspect.digest(release / 'inspect-api-web-release.py')}
        (release / 'release.json').write_text(json.dumps(manifest))
        (release / 'compose.override.json').write_text(json.dumps({'services': {service: {'image': item['tag']} for service, item in images.items()}}))
        state = {service: {'container': str(index) * 64, 'image': IMAGE, 'running': True,
                          'started': 'synthetic-time', 'restarts': 0}
                 for index, service in enumerate(('api', 'web', 'db', 'pki'), 1)}
        return release, app, state

    def test_candidate_and_rollback_preserve_other_services_and_image_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            release, app, state = self.fixture(directory)
            with patch.object(inspect, 'snapshot', return_value=state), patch.object(inspect, 'output', side_effect=candidate_output):
                saved = inspect.inspect_release(release, app, require_images=True)
                rollback = release / 'rollback.json'
                inspect.write_rollback(rollback, saved)
                self.assertEqual(rollback.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(FileExistsError):
                    inspect.write_rollback(rollback, saved)
                state['api']['image'] = state['web']['image'] = NEW_IMAGE
                inspect.inspect_release(release, app, require_images=True, verify_preserved=rollback, verify_candidate=True)
                with self.assertRaises(ValueError):
                    inspect.inspect_release(release, app, require_images=True, verify_preserved=rollback, verify_rollback=True)
                state['api']['image'] = state['web']['image'] = IMAGE
                inspect.inspect_release(release, app, require_images=False, verify_preserved=rollback, verify_rollback=True)
                state['pki']['restarts'] = 1
                with self.assertRaises(ValueError):
                    inspect.inspect_release(release, app, require_images=False, verify_preserved=rollback)

    def test_corruption_changed_sources_and_extra_services_fail_closed(self):
        for change in ('archive', 'source', 'override', 'image', 'symlink', 'traversal'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                release, app, state = self.fixture(directory)
                if change == 'archive':
                    (release / 'images.tar').write_bytes(b'corrupt')
                elif change == 'source':
                    (app / 'compose.yaml').write_bytes(b'changed')
                elif change == 'override':
                    (release / 'compose.override.json').write_text('{"services":{"pki":{"image":"unexpected"}}}')
                elif change == 'symlink':
                    (app / 'backend/src/app.py').unlink()
                    (app / 'backend/src/app.py').symlink_to(app / 'compose.yaml')
                elif change == 'traversal':
                    manifest = json.loads((release / 'release.json').read_text())
                    manifest['baseline_files']['../private.key'] = 'a' * 64
                    (release / 'release.json').write_text(json.dumps(manifest))
                with patch.object(inspect, 'snapshot', return_value=state), patch.object(inspect, 'output', return_value=IMAGE) as docker:
                    with self.assertRaises(ValueError):
                        inspect.inspect_release(release, app, require_images=True)
                    if change != 'image':
                        docker.assert_not_called()

    def test_container_inspection_is_read_only_and_excludes_environment(self):
        commands = []
        def fake(*arguments):
            commands.append(arguments)
            if arguments[0] == 'compose':
                return '1' * 64
            return json.dumps({'image': IMAGE, 'running': True, 'started': 'synthetic-time', 'restarts': 0})
        with patch.object(inspect, 'output', side_effect=fake):
            inspect.snapshot(Path('/synthetic/app'))
        self.assertEqual(len(commands), 8)
        self.assertTrue(all(command[0] in ('compose', 'inspect') for command in commands))
        self.assertTrue(all('ps' in command and '--quiet' in command for command in commands if command[0] == 'compose'))
        self.assertNotIn('.Config', inspect.FORMAT)
        self.assertNotIn('.Env', inspect.FORMAT)

    def test_platform_mismatch_is_rejected_before_cutover(self):
        with tempfile.TemporaryDirectory() as directory:
            release, app, state = self.fixture(directory)
            def different_platform(*arguments):
                if arguments[3] == '{{.Os}}/{{.Architecture}}':
                    return 'linux/amd64' if arguments[-1] == IMAGE else 'linux/arm64'
                return NEW_IMAGE
            with patch.object(inspect, 'snapshot', return_value=state), patch.object(inspect, 'output', side_effect=different_platform):
                with self.assertRaises(ValueError):
                    inspect.inspect_release(release, app, require_images=True)

    def test_private_file_names_are_not_baseline_inputs(self):
        for name in ('backend/src/.env', 'backend/src/private.key', 'frontend/src/client.ovpn', '../secrets', '/etc/secret'):
            self.assertFalse(inspect.allowed(name))

    def test_rollback_refuses_unprotected_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / 'public'
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
            with self.assertRaises(ValueError):
                inspect.write_rollback(parent / 'rollback.json', {})
            self.assertFalse((parent / 'rollback.json').exists())

    def test_public_context_modes_are_runtime_readable_inside_private_release(self):
        with restrictive_umask(), tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            contexts = root / 'build'
            prepare.export_contexts(contexts, {'frontend/Caddyfile': b'test', 'backend/migrations/versions/test.py': b'test', 'compose.yaml': b'not exported'})
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((contexts/'frontend/Caddyfile').stat().st_mode & 0o777, 0o644)
            self.assertEqual((contexts/'backend/migrations/versions/test.py').stat().st_mode & 0o777, 0o644)
            for name in ('', 'backend', 'backend/migrations', 'backend/migrations/versions', 'frontend'):
                self.assertEqual((contexts/name).stat().st_mode & 0o777, 0o755)
            self.assertFalse((contexts/'compose.yaml').exists())


if __name__ == '__main__':
    unittest.main()
