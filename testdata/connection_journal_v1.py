"""Nonsecret handoff intent, under the plugin's existing account lock."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from urllib.parse import urlsplit

try:
    from .connection_offer import HandoffError
except ImportError:
    from connection_offer import HandoffError


BINDING = ('version', 'request_id', 'connection_id', 'plugin_id', 'profile')


def fingerprint(session):
    return hashlib.sha256(json.dumps(session, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def object_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise HandoffError('invalid')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HandoffError('invalid') from None


def reconcile_request(raw, instance):
    if not isinstance(raw, (str, bytes, bytearray)) or len(raw) > 16384:
        raise HandoffError('invalid')
    value = object_json(raw)
    if (not isinstance(value, dict) or set(value) != set(BINDING) | {'action', 'origin', 'ship'}
            or type(value['version']) is not int or value['version'] != 1 or value['action'] != 'reconcile'
            or any(not isinstance(value[k], str) for k in set(value) - {'version'})
            or any(not re.fullmatch('[0-9a-f]{32}', value[k]) for k in ('request_id', 'connection_id'))):
        raise HandoffError('invalid')
    if (value['plugin_id'], value['profile']) != (instance.plugin_id, instance.profile):
        raise HandoffError('wrong-recipient')
    # Origin/ship must match durable local evidence before any account operation.
    # They are not used as filesystem paths or new network destinations here.
    if len(value['origin']) > 2048 or len(value['ship']) > 128:
        raise HandoffError('invalid')
    return value


def reconciled(value, outcome):
    return {k: value[k] for k in BINDING} | {'status': 'reconciled', 'outcome': outcome}


class Journal:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'application-attempts.json'
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            self.entries = []
            return
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise HandoffError('journal')
            raw = stream.read(262145)
        value = object_json(raw) if len(raw) <= 262144 else None
        if (not isinstance(value, dict) or set(value) != {'version', 'attempts'}
                or type(value['version']) is not int or value['version'] != 1
                or not isinstance(value['attempts'], list) or len(value['attempts']) > 64):
            raise HandoffError('journal')
        ids = set()
        for row in value['attempts']:
            if (not isinstance(row, dict) or set(row) != set(BINDING) | {'origin', 'ship', 'fingerprint', 'phase', 'outcome'}
                    or type(row['version']) is not int or row['version'] != 1
                    or any(not isinstance(row[k], str) for k in set(row) - {'version'})
                    or any(not re.fullmatch('[0-9a-f]{32}', row[k]) for k in ('request_id', 'connection_id'))
                    or not re.fullmatch('[0-9a-f]{64}', row['fingerprint'])
                    or row['request_id'] in ids or len(row['origin']) > 2048 or len(row['ship']) > 128
                    or not re.fullmatch('[a-z][a-z0-9-]{0,31}', row['profile'])
                    or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,127}', row['plugin_id'])
                    or row['phase'] not in ('pending', 'accepted', 'reconciled')
                    or (row['outcome'] not in ('removed', 'absent', 'superseded') if row['phase'] == 'reconciled' else row['outcome'] != '')):
                raise HandoffError('journal')
            ids.add(row['request_id'])
        self.entries = value['attempts']

    def save(self):
        raw = json.dumps({'version': 1, 'attempts': self.entries}, separators=(',', ':')).encode()
        if len(raw) > 262144:
            raise HandoffError('journal')
        fd, temp = tempfile.mkstemp(prefix='.application-attempt-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            sync_directory(self.root)
        finally:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass

    def begin(self, offer, session):
        if len(self.entries) >= 64 or any(row['request_id'] == offer['request_id'] or
                row['origin'] == session['url'] and row['phase'] != 'reconciled' for row in self.entries):
            raise HandoffError('recovery-required')
        row = {k: offer[k] for k in BINDING} | {'origin': session['url'], 'ship': session['ship'],
              'fingerprint': fingerprint(session), 'phase': 'pending', 'outcome': ''}
        self.entries.append(row)
        self.save()  # Must precede the first possible credential write.
        return row

    def find(self, value):
        row = next((row for row in self.entries if row['request_id'] == value['request_id']), None)
        if row is not None and any(row[k] != value[k] for k in BINDING + ('origin', 'ship')):
            raise HandoffError('attempt-changed')
        return row

    def finish(self, row, outcome):
        row['phase'], row['outcome'] = 'reconciled', outcome
        self.save()


def sync_directory(root):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def secret_present(instance, origin, runner=subprocess.run):
    """Search metadata only. Locked/duplicate items are not treated as absent."""
    args = ['/usr/bin/busctl', '--user', '--json=short', '--timeout=5', 'call',
            'org.freedesktop.secrets', '/org/freedesktop/secrets',
            'org.freedesktop.Secret.Service', 'SearchItems', 'a{ss}', '2',
            'application', instance.state_name, 'origin', origin]
    try:
        result = runner(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, timeout=6, check=False)
        value = object_json(result.stdout) if result.returncode == 0 and len(result.stdout) <= 16384 else None
        if (not isinstance(value, dict) or set(value) != {'type', 'data'} or value['type'] != 'aoao'
                or not isinstance(value['data'], list) or len(value['data']) != 2
                or any(not isinstance(part, list) for part in value['data'])
                or value['data'][1] or len(value['data'][0]) > 1
                or any(not isinstance(path, str) or not re.fullmatch(r'/[A-Za-z0-9_/]+', path) for path in value['data'][0])):
            raise HandoffError('keyring-unavailable')
        return bool(value['data'][0])
    except (OSError, subprocess.TimeoutExpired):
        raise HandoffError('keyring-unavailable') from None
