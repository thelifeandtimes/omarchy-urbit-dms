"""Durable send intent; no automatic replay and no session credentials.

The body is a private local draft. Public summaries omit it. A journal failure
prevents dispatch; unknown formats and unresolved writes survive helper reloads.
"""
import json
import os
import re
import stat
import tempfile
import uuid

from session_store import SessionStore, StoreError, SHIP, origin

LIMIT = 4 * 1024 * 1024


class OutboundJournal:
    def __init__(self, root=None):
        self.store = SessionStore(root=root)
        self.path = self.store.root / 'outbound-messages.json'

    def _read(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return []
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
                raise StoreError('Outbound message history is not private')
            raw = stream.read(LIMIT + 1)
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError('duplicate field')
                result[key] = value
            return result
        try:
            v = json.loads(raw, object_pairs_hook=pairs) if len(raw) <= LIMIT else None
            if not isinstance(v, dict) or set(v) != {'version', 'messages'} or type(v['version']) is not int or v['version'] != 1 or not isinstance(v['messages'], list) or len(v['messages']) > 192:
                raise ValueError('unknown format')
            ids = set()
            for row in v['messages']:
                if not isinstance(row, dict) or set(row) != {'id','origin','ship','peer','message','phase'}:
                    raise ValueError('unknown record')
                if not isinstance(row['id'], str) or not re.fullmatch('[0-9a-f]{32}', row['id']) or row['id'] in ids:
                    raise ValueError('invalid identity')
                ids.add(row['id'])
                if not isinstance(row['origin'], str) or origin(row['origin']) != row['origin'] or any(not isinstance(row[k], str) or not SHIP.fullmatch(row[k]) for k in ('ship','peer')) or row['phase'] not in ('uncertain','acknowledged','rejected','observed'):
                    raise ValueError('invalid binding')
                m = row['message']
                if not isinstance(m, dict) or set(m) != {'id','author','sent','text'} or m['author'] != row['ship'] or not isinstance(m['id'], str) or not m['id'].startswith(row['ship']+'/') or len(m['id']) > 256 or type(m['sent']) is not int or m['sent'] < 0 or not isinstance(m['text'], str) or not 1 <= len(m['text']) <= 10000:
                    raise ValueError('invalid message')
            return v['messages']
        except (ValueError, TypeError, UnicodeError, RecursionError) as error:
            raise StoreError('Outbound message history needs review; no message was sent') from error

    def _write(self, rows):
        raw = json.dumps({'version':1,'messages':rows},ensure_ascii=False,separators=(',',':')).encode()
        if len(raw) > LIMIT:
            raise StoreError('Outbound message history is full; review unresolved messages')
        fd, name = tempfile.mkstemp(prefix='.outbound-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(),0o600);stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.replace(name,self.path)
            directory = os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:
            try:os.unlink(name)
            except FileNotFoundError:pass

    def begin(self, url, ship, peer, message):
        if origin(url) != url or not SHIP.fullmatch(ship) or not SHIP.fullmatch(peer):
            raise StoreError('Cannot bind the outbound message to its ship')
        if not isinstance(message, dict) or set(message) != {'id','author','sent','text'} or message['author'] != ship or not isinstance(message['id'], str) or not message['id'].startswith(ship+'/') or len(message['id']) > 256 or type(message['sent']) is not int or message['sent'] < 0 or not isinstance(message['text'], str) or not 1 <= len(message['text']) <= 10000:
            raise StoreError('Invalid outbound message intent')
        with self.store.locked():
            rows=self._read();pending=[r for r in rows if r['phase']=='uncertain']
            if len(pending)>=128:
                raise StoreError('Review unresolved messages before sending more')
            rows=pending+[r for r in rows if r['phase']!='uncertain'][-63:]
            ident=uuid.uuid4().hex
            rows.append({'id':ident,'origin':url,'ship':ship,'peer':peer,'message':dict(message),'phase':'uncertain'})
            self._write(rows)
            return ident

    def finish(self, ident, phase):
        if phase not in ('acknowledged','rejected'):
            raise StoreError('A positive receipt or rejection is required')
        with self.store.locked():
            rows=self._read();row=next((r for r in rows if r['id']==ident),None)
            if row is None:
                raise StoreError('Outbound message receipt lost its saved intent')
            if row['phase']=='uncertain':
                row['phase']=phase;self._write(rows)
            elif row['phase'] not in (phase,'observed'):
                raise StoreError('Conflicting outbound message receipts')

    def observe(self,url,ship,peer,messages):
        """An exact authenticated history/fact match resolves a send; absence never does."""
        with self.store.locked():
            rows=self._read();changed=False
            for row in rows:
                if row['phase']!='uncertain' or (row['origin'],row['ship'],row['peer'])!=(url,ship,peer):continue
                for message in messages:
                    if all(message.get(k)==v for k,v in row['message'].items()):
                        row['phase']='observed';changed=True;break
            if changed:self._write(rows)

    def unresolved(self):
        with self.store.locked():
            return [{k:row[k] for k in ('id','origin','ship','peer','phase')} | {'messageId':row['message']['id']} for row in self._read() if row['phase']=='uncertain']
