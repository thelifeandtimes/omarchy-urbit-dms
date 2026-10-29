"""Private invitation/read-marker intents. Never replay an uncertain action."""
import json
import os
import re
import stat
import tempfile
import uuid
from session_store import SessionStore, StoreError, SHIP, origin

LIMIT = 262144

class EffectsJournal:
    def __init__(self, root=None):
        self.store = SessionStore(root=root)
        self.path = self.store.root / 'conversation-effects.json'

    def _read(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return []
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
                raise StoreError('Conversation action history is not private')
            raw = stream.read(LIMIT + 1)
        def pairs(items):
            result = {}
            for key,value in items:
                if key in result:raise ValueError('duplicate field')
                result[key] = value
            return result
        try:
            value = json.loads(raw,object_pairs_hook=pairs) if len(raw) <= LIMIT else None
            if not isinstance(value,dict) or set(value) != {'version','effects'} or type(value['version']) is not int or value['version'] not in (1,2) or not isinstance(value['effects'],list) or len(value['effects']) > 192:
                raise ValueError('unknown format')
            ids = set()
            for row in value['effects']:
                if not isinstance(row,dict) or set(row) != {'id','origin','ship','peer','kind','phase'}:
                    raise ValueError('unknown record')
                if not isinstance(row['id'],str) or not re.fullmatch('[0-9a-f]{32}',row['id']) or row['id'] in ids:
                    raise ValueError('invalid identity')
                ids.add(row['id'])
                if not isinstance(row['origin'],str) or origin(row['origin']) != row['origin'] or any(not isinstance(row[k],str) or not SHIP.fullmatch(row[k]) for k in ('ship','peer')) or row['kind'] not in ('accept-invitation','mark-read') or row['phase'] not in (('uncertain','acknowledged','rejected') if value['version']==1 else ('uncertain','acknowledged','rejected','state-confirmed')):
                    raise ValueError('invalid binding')
            return value['effects']
        except (ValueError,TypeError,UnicodeError,RecursionError) as error:
            raise StoreError('Conversation action history needs review; no action was sent') from error

    def _write(self, rows):
        raw=json.dumps({'version':2,'effects':rows},separators=(',',':')).encode()
        if len(raw)>LIMIT:raise StoreError('Conversation action history is full')
        fd,name=tempfile.mkstemp(prefix='.conversation-effect-',dir=self.path.parent)
        try:
            with os.fdopen(fd,'wb') as stream:
                os.fchmod(stream.fileno(),0o600);stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.replace(name,self.path)
            directory=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:
            try:os.unlink(name)
            except FileNotFoundError:pass

    def begin(self,url,ship,peer,kind):
        if origin(url)!=url or not SHIP.fullmatch(ship) or not SHIP.fullmatch(peer) or kind not in ('accept-invitation','mark-read'):
            raise StoreError('Cannot bind the conversation action to its ship')
        with self.store.locked():
            rows=self._read();pending=[r for r in rows if r['phase']=='uncertain']
            if len(pending)>=128:raise StoreError('Review unresolved conversation actions before sending more')
            rows=pending+[r for r in rows if r['phase']!='uncertain'][-63:]
            ident=uuid.uuid4().hex
            rows.append({'id':ident,'origin':url,'ship':ship,'peer':peer,'kind':kind,'phase':'uncertain'})
            self._write(rows);return ident

    def finish(self,ident,phase):
        if phase not in ('acknowledged','rejected'):raise StoreError('A positive receipt or rejection is required')
        with self.store.locked():
            rows=self._read();row=next((r for r in rows if r['id']==ident),None)
            if row is None:raise StoreError('Conversation receipt lost its saved intent')
            if row['phase'] in ('uncertain','state-confirmed'):row['phase']=phase;self._write(rows)
            elif row['phase']!=phase:raise StoreError('Conflicting conversation action receipts')

    def candidates(self,url,ship):
        with self.store.locked():
            return [r['id'] for r in self._read() if r['phase']=='uncertain' and (r['origin'],r['ship'])==(url,ship)]

    def observe(self,url,ship,candidates,peers=None,activity=None):
        """Confirm the current desired state, not a lost operation acknowledgement.

        Only intents that existed before the authenticated read may be resolved.
        Missing entries/counts and a zero coerced from malformed data prove nothing.
        """
        accepted=set(peers) if isinstance(peers,list) and all(isinstance(p,str) and SHIP.fullmatch(p) for p in peers) else set()
        read=set()
        if isinstance(activity,dict):
            for source,summary in activity.items():
                if not isinstance(source,str) or not source.startswith('ship/') or not SHIP.fullmatch(source[5:]) or not isinstance(summary,dict):continue
                # v4 unread=null explicitly says the main stream is read. Child
                # thread counts may remain because the original action is shallow.
                if 'unread' in summary and summary['unread'] is None and type(summary.get('count')) is int and summary['count']>=0 and type(summary.get('notify-count')) is int and 0<=summary['notify-count']<=summary['count']:
                    read.add(source[5:])
        wanted=set(candidates)
        with self.store.locked():
            rows=self._read();changed=False
            for row in rows:
                if row['phase']!='uncertain' or row['id'] not in wanted or (row['origin'],row['ship'])!=(url,ship):continue
                if row['peer'] in (accepted if row['kind']=='accept-invitation' else read):
                    row['phase']='state-confirmed';changed=True
            if changed:self._write(rows)
            return changed

    def unresolved(self):
        with self.store.locked():return [dict(r) for r in self._read() if r['phase']=='uncertain']
