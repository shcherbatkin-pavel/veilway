"""Bounded local VPN accounting. No network, VPN files, addresses or raw names stored."""
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

MODES = {'aws-direct', 'yc-direct', 'yc-aws-multihop'}
MAX_EVENTS = 100000
MAX_STATUS = 4 * 1024 * 1024
MAX_CONNECTIONS = 2000


def stamp(seconds=None):
    return datetime.fromtimestamp(int(seconds) if seconds is not None else int(__import__('time').time()), timezone.utc).isoformat().replace('+00:00', 'Z')


def integer(value):
    result = int(value)
    if not 0 <= result < 2**63:
        raise ValueError('invalid counter')
    return result


def serial(value):
    raw = value.replace(':', '').upper()
    if not raw or len(raw) > 40 or any(c not in '0123456789ABCDEF' for c in raw):
        raise ValueError('invalid serial')
    result = format(int(raw, 16), 'X')
    if result == '0':
        raise ValueError('invalid serial')
    return result


def name_hash(value):
    if not value or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise ValueError('invalid name')
    return hashlib.sha256(value.encode()).hexdigest()


def profile_hint(name):
    try:
        return str(uuid.UUID(name))
    except ValueError:
        return None


def private_regular(path):
    if path.is_symlink():
        raise ValueError('symlink accounting input')


class Store:
    def __init__(self, mode, *, state_root=Path('/var/lib/veilway-analytics'), runtime_root=Path('/run/veilway-analytics')):
        if mode not in MODES:
            raise ValueError('invalid mode')
        self.mode = mode
        self.root = state_root / mode
        self.runtime = runtime_root / mode
        self.path = self.root / 'sessions.sqlite'

    @contextmanager
    def database(self):
        private_regular(self.path)
        private_regular(self.root)
        db = sqlite3.connect(self.path, timeout=0.15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, epoch TEXT NOT NULL, name_hash TEXT NOT NULL, peer_hash TEXT NOT NULL, started INTEGER NOT NULL, serial TEXT, profile_id TEXT, last_seen INTEGER NOT NULL, rx INTEGER NOT NULL, tx INTEGER NOT NULL, closed INTEGER NOT NULL DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, session_id TEXT NOT NULL, payload TEXT NOT NULL)')
            yield db
            db.commit()
        finally:
            db.close()

    def loss(self):
        # Small protected quality marker, not a journal of inputs or exceptions.
        try:
            path = self.runtime / 'capture-errors'
            if path.exists() and path.stat().st_size >= 65536:
                return
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            try:
                os.write(descriptor, b'x')
            finally:
                os.close(descriptor)
        except OSError:
            pass

    def errors(self):
        path = self.runtime / 'capture-errors'
        private_regular(path)
        return path.stat().st_size if path.exists() else 0

    def epoch(self):
        path = self.runtime / 'epoch'
        private_regular(path)
        return str(uuid.UUID(path.read_text().strip()))

    def data(self, row, observed, rx=None, tx=None):
        return dict(session_id=row['id'], epoch=row['epoch'], mode=self.mode,
            certificate_serial=row['serial'], profile_id=row['profile_id'], started_at=stamp(row['started']),
            observed_at=stamp(observed), bytes_received=rx if rx is not None else row['rx'],
            bytes_sent=tx if tx is not None else row['tx'])

    def event(self, db, row, kind, observed, rx=None, tx=None):
        if db.execute('SELECT count(*) FROM events').fetchone()[0] >= MAX_EVENTS:
            raise ValueError('accounting queue full')
        identifier = str(uuid.uuid4())
        value = dict(self.data(row, observed, rx, tx), id=identifier, kind=kind)
        db.execute('INSERT INTO events (id,session_id,payload) VALUES (?,?,?)', (identifier,row['id'],json.dumps(value,separators=(',',':'))))

    def hook(self, kind, env):
        if kind == 'up':
            with self.database() as db:
                for row in db.execute('SELECT * FROM connections WHERE closed=0').fetchall():
                    self.event(db, row, 'interrupted', row['last_seen'])
                db.execute('UPDATE connections SET closed=1 WHERE closed=0')
            epoch = self.runtime / 'epoch'
            private_regular(epoch)
            temporary = self.runtime / 'epoch.new'
            with temporary.open('x') as stream:
                stream.write(str(uuid.uuid4()))
            temporary.chmod(0o600)
            started_path = self.runtime / 'epoch-started'
            private_regular(started_path)
            started_path.write_text(stamp())
            started_path.chmod(0o600)
            os.replace(temporary, epoch)
            return
        if kind == 'down':
            with self.database() as db:
                for row in db.execute('SELECT * FROM connections WHERE closed=0 AND epoch=?', (self.epoch(),)).fetchall():
                    self.event(db, row, 'interrupted', row['last_seen'])
                db.execute('UPDATE connections SET closed=1 WHERE epoch=?', (self.epoch(),))
            return
        if kind not in {'client-connect', 'client-disconnect'}:
            raise ValueError('invalid callback')
        name = env.get('common_name', '')
        hashed = name_hash(name)
        started = integer(env.get('time_unix', ''))
        # Only a digest is retained; floating addresses are handled by the fallback below.
        peer = hashlib.sha256((env.get('trusted_ip', env.get('trusted_ip6', '')) + ':' + env.get('trusted_port','')).encode()).hexdigest()
        epoch = self.epoch()
        with self.database() as db:
            if kind == 'client-connect':
                value = env.get('tls_serial_hex_0', '')
                number = serial(value) if value else format(integer(env.get('tls_serial_0', '')), 'X')
                if number == '0':
                    raise ValueError('missing certificate identity')
                identifier = str(uuid.uuid4())
                db.execute('INSERT INTO connections (id,epoch,name_hash,peer_hash,started,serial,profile_id,last_seen,rx,tx) VALUES (?,?,?,?,?,?,?,?,0,0)',
                    (identifier, epoch, hashed, peer, started, number, profile_hint(name), started))
                row = db.execute('SELECT * FROM connections WHERE id=?', (identifier,)).fetchone()
                self.event(db, row, 'start', started)
            else:
                candidates = db.execute('SELECT * FROM connections WHERE epoch=? AND name_hash=? AND started=? AND closed=0',
                                        (epoch,hashed,started)).fetchall()
                exact = [r for r in candidates if r['peer_hash'] == peer]
                row = exact[0] if len(exact)==1 else candidates[0] if len(candidates)==1 else None
                if row is None:
                    raise ValueError('ambiguous disconnect')
                ended = started + integer(env.get('time_duration', ''))
                rx, tx = integer(env.get('bytes_received','')), integer(env.get('bytes_sent',''))
                self.event(db, row, 'end', ended, max(rx,row['rx']), max(tx,row['tx']))
                db.execute('UPDATE connections SET closed=1,last_seen=?,rx=?,tx=? WHERE id=?',
                           (ended,max(rx,row['rx']),max(tx,row['tx']),row['id']))

    def snapshot(self, *, now=None):
        now = integer(now if now is not None else __import__('time').time())
        status = self.runtime / 'status.tsv'
        private_regular(status)
        if status.stat().st_size > MAX_STATUS:
            raise ValueError('oversize status')
        observed, clients = parse_status(status.read_text())
        if observed > now+60 or observed < now-30:
            raise ValueError('stale status')
        epoch = self.epoch()
        started_path = self.runtime / 'epoch-started'
        private_regular(started_path)
        epoch_started = int(datetime.fromisoformat(started_path.read_text().replace('Z','+00:00')).timestamp())
        if observed <= epoch_started or any(started < epoch_started for _, started, _, _ in clients):
            raise ValueError('status belongs to previous server epoch')
        sessions = []
        completed = 0
        with self.database() as db:
            for name, started, rx, tx in clients:
                hashed = name_hash(name)
                matches = db.execute('SELECT * FROM connections WHERE epoch=? AND name_hash=? AND started=?',
                                     (epoch,hashed,started)).fetchall()
                if any(row['closed'] for row in matches):
                    completed += 1
                    continue
                if len(matches) > 1:
                    self.loss();continue
                row = matches[0] if matches else None
                if row is None:
                    identifier = str(uuid.uuid4())
                    db.execute('INSERT INTO connections (id,epoch,name_hash,peer_hash,started,profile_id,last_seen,rx,tx) VALUES (?,?,?,?,?,?,?,?,?)',
                               (identifier,epoch,hashed,'',started,profile_hint(name),observed,rx,tx))
                    row = db.execute('SELECT * FROM connections WHERE id=?',(identifier,)).fetchone()
                db.execute('UPDATE connections SET last_seen=?,rx=?,tx=? WHERE id=?',
                           (max(observed,row['last_seen']),max(rx,row['rx']),max(tx,row['tx']),row['id']))
                sessions.append(self.data(row, observed, max(rx,row['rx']), max(tx,row['tx'])))
        return dict(mode=self.mode,status='available',epoch=epoch,observed_at=stamp(observed),
                    connection_count=len(clients)-completed,sessions=sessions,capture_errors=self.errors())

    def pending(self, limit):
        with self.database() as db:
            return [json.loads(r['payload']) for r in db.execute('SELECT payload FROM events ORDER BY seq LIMIT ?', (limit,))]

    def acknowledge(self, identifiers):
        with self.database() as db:
            db.executemany('DELETE FROM events WHERE id=?', ((i,) for i in identifiers))
            # Retain a short tombstone while a still-fresh status file can include
            # a disconnected client. Otherwise ACK could resurrect its session.
            cutoff = int(__import__('time').time()) - 60
            db.execute('DELETE FROM connections WHERE closed=1 AND last_seen<? AND NOT EXISTS (SELECT 1 FROM events WHERE events.session_id=connections.id)', (cutoff,))


def parse_status(text):
    observed, header, clients, ended = None, None, [], False
    for parts in csv.reader(text.splitlines(), delimiter='\t'):
        if not parts:
            continue
        if parts[0]=='TIME' and len(parts)==3:
            observed=integer(parts[2])
        elif parts[:2]==['HEADER','CLIENT_LIST']:
            header={name:i for i,name in enumerate(parts[2:])}
        elif parts[0]=='CLIENT_LIST':
            if header is None:
                raise ValueError('missing status header')
            row=parts[1:]
            name=row[header['Common Name']]
            started=integer(row[header['Connected Since (time_t)']])
            rx,tx=integer(row[header['Bytes Received']]),integer(row[header['Bytes Sent']])
            if observed is None or started>observed:
                raise ValueError('invalid status time')
            clients.append((name,started,rx,tx))
            if len(clients)>MAX_CONNECTIONS:
                raise ValueError('connection limit')
        elif parts[0]=='END':
            ended=True
    if observed is None or header is None or not ended:
        raise ValueError('incomplete status')
    if len({(name,started) for name,started,_,_ in clients})!=len(clients):
        raise ValueError('ambiguous status identity')
    return observed,clients


def drop_privileges():
    if os.geteuid()==0:
        os.setgroups([]);os.setgid(900);os.setuid(900)
    if os.geteuid()!=900:
        raise PermissionError('accounting UID required')
    os.umask(0o077)
