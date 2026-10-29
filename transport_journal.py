"""Private durable Eyre channel ownership. No credentials or network operations.

The bridge uses this internal journal before channel dispatch. A bound transport adapter
must save begin() before its first PUT and record deletion only after the exact
HTTP request succeeds. Recovery never resends a Gall/application action.
"""
import contextlib
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from instance import INSTANCE
from session_store import SessionStore,StoreError,origin,SHIP,valid_session

LIMIT=2*1024*1024
MAX_CHANNELS=1024
ID=re.compile(r'[0-9a-f]{32}\Z')
HASH=re.compile(r'[0-9a-f]{64}\Z')
BOOT=re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
NAMESPACE=re.compile(r'pid:\[[0-9]+\]\Z')
CHANNEL=re.compile(r'urbit-dms-[0-9a-f]{24}\Z')


def require(value,message='Saved transport ownership needs review; it was preserved'):
    if not value:raise StoreError(message)


def exact(value,keys):require(type(value) is dict and set(value)==set(keys))
def token(value,pattern=ID):require(type(value) is str and pattern.fullmatch(value))
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def session_ref(session):
    valid_session(session,check_expiry=False)
    return hashlib.sha256(canonical({k:session[k] for k in ('url','ship','cookieName','cookieValue')})).hexdigest()


def process_identity():
    # Start ticks prevent PID reuse; namespace identity prevents treating a PID
    # from another process namespace as evidence of death on the same boot.
    values=Path('/proc/self/stat').read_text().split(') ',1)[1].split()
    return {'pid':os.getpid(),'start':values[19],
            'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'namespace':os.readlink('/proc/self/ns/pid')}


def valid_owner(value):
    exact(value,('id','pid','start','boot','namespace'))
    token(value['id']);token(value['boot'],BOOT);token(value['namespace'],NAMESPACE)
    require(type(value['pid']) is int and value['pid']>0 and type(value['start']) is str and re.fullmatch('[0-9]{1,24}',value['start']))


def owner_status(owner):
    valid_owner(owner)
    try:
        now=process_identity()
        if owner['boot']!=now['boot']:return 'dead'
        if owner['namespace']!=now['namespace']:return 'unknown'
        try:values=Path('/proc')/str(owner['pid'])/'stat';fields=values.read_text().split(') ',1)[1].split()
        except (FileNotFoundError,ProcessLookupError):return 'dead'
        return 'dead' if fields[19]!=owner['start'] or fields[0]=='Z' else 'alive'
    except (OSError,ValueError,IndexError):return 'unknown'


class Lease:
    def __init__(self,journal):
        self.journal=journal;self.owner={'id':uuid.uuid4().hex,**process_identity()};self.live=True


class TransportJournal:
    def __init__(self,root=None,instance=INSTANCE):
        self.store=SessionStore(root=root,instance=instance)
        self.root=self.store.root/'transport'
        self.path=self.root/'channels.json'
        self.instance=instance

    def _directory(self):
        info=self.root.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)==0o700,'Transport state must be a private owned directory')
        require(self.root.resolve()==self.root.absolute(),'Transport state path must not contain symlinks')

    def _open(self,path,flags):
        fd=os.open(path,flags|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,0o600)
        info=os.fstat(fd)
        if not (stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and info.st_nlink==1 and stat.S_IMODE(info.st_mode)==0o600 and info.st_size<=LIMIT):
            os.close(fd);raise StoreError('Transport state must be a bounded private regular file')
        return fd

    def _validate(self,value):
        exact(value,('version','plugin_id','profile','channels'))
        require(type(value['version']) is int and value['version']==1)
        require((value['plugin_id'],value['profile'])==(self.instance.plugin_id,self.instance.profile),'Transport state belongs to another plugin instance')
        require(type(value['channels']) is list and len(value['channels'])<=MAX_CHANNELS)
        ids=set();channels=set();requests=set()
        for row in value['channels']:
            exact(row,('id','origin','ship','session_ref','channel','owner','phase','cleanup_request','cleanup_actor','delete_status'))
            token(row['id']);token(row['session_ref'],HASH);token(row['channel'],CHANNEL);valid_owner(row['owner'])
            require(row['id'] not in ids and row['channel'] not in channels);ids.add(row['id']);channels.add(row['channel'])
            require(type(row['origin']) is str and origin(row['origin'])==row['origin'] and type(row['ship']) is str and SHIP.fullmatch(row['ship']))
            require(row['phase'] in ('open','closing','closed'))
            if row['phase']=='open':
                require(row['cleanup_request'] is None and row['cleanup_actor'] is None and row['delete_status'] is None)
            else:
                token(row['cleanup_request']);valid_owner(row['cleanup_actor'])
                require(row['cleanup_request'] not in requests);requests.add(row['cleanup_request'])
                if row['phase']=='closing':require(row['delete_status'] is None)
                else:require(type(row['delete_status']) is int and row['delete_status'] in (200,204,401,403))
        return value

    def _read(self):
        self._directory()
        # Existing directory + missing journal is never a fresh state. A crash
        # during initialization must be reviewed before any channel is dispatched.
        try:fd=self._open(self.path,os.O_RDONLY)
        except FileNotFoundError:raise StoreError('Transport journal is missing; prior ownership must not be recreated') from None
        with os.fdopen(fd,'rb') as f:raw=f.read(LIMIT+1)
        require(len(raw)<=LIMIT)
        def pairs(items):
            obj={}
            for key,value in items:require(key not in obj);obj[key]=value
            return obj
        try:value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError,UnicodeError,RecursionError):raise StoreError('Transport journal is unreadable; it was preserved') from None
        return self._validate(value)

    def _sync(self):
        fd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:os.fsync(fd)
        finally:os.close(fd)

    def _write(self,value):
        self._validate(value);raw=canonical(value)+b'\n';require(len(raw)<=LIMIT)
        path=self.root/('.pending-'+uuid.uuid4().hex)
        fd=self._open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
        # Retain failed temporary files. They never replace authoritative state
        # implicitly; fsync failure remains an uncertain local result for retry.
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(path,self.path);self._sync()

    @contextlib.contextmanager
    def owner(self):
        # Serialize first creation against normal account operations. No keyring
        # access occurs here. The separate lease remains held for helper lifetime.
        with self.store.locked():
            try:self.root.mkdir(mode=0o700)
            except FileExistsError:self._directory()
            else:
                fd=os.open(self.root.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
                try:os.fsync(fd)
                finally:os.close(fd)
                self._write({'version':1,'plugin_id':self.instance.plugin_id,'profile':self.instance.profile,'channels':[]})
            self._read()
        fd=self._open(self.root/'owner.lock',os.O_RDWR|os.O_CREAT);lease=None
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);lease=Lease(self)
            yield lease
        finally:
            if lease:lease.live=False
            os.close(fd)

    def _lease(self,lease):
        require(lease.journal is self and lease.live and lease.owner['pid']==os.getpid(),'A live transport owner lease is required')
        require({k:v for k,v in lease.owner.items() if k!='id'}==process_identity(),'Transport owner process changed')

    def status(self):
        # Observation has no directory-creation or lock-file side effects.
        if not self.root.exists() and not self.root.is_symlink():return None
        return copy.deepcopy(self._read())

    def begin(self,lease,session):
        self._lease(lease);valid_session(session);value=self._read()
        require(not any(row['phase']!='closed' for row in value['channels']),'Clean up the prior channel before opening a replacement')
        require(len(value['channels'])<MAX_CHANNELS,'Transport receipt history is full; preserve it before opening another channel')
        row={'id':uuid.uuid4().hex,'origin':session['url'],'ship':session['ship'],'session_ref':session_ref(session),
             'channel':'urbit-dms-'+uuid.uuid4().hex[:24],'owner':copy.deepcopy(lease.owner),'phase':'open',
             'cleanup_request':None,'cleanup_actor':None,'delete_status':None}
        value['channels'].append(row);self._write(value)
        return copy.deepcopy(row)

    def _find(self,value,ident):
        token(ident);row=next((row for row in value['channels'] if row['id']==ident),None)
        require(row is not None,'Transport cleanup lost its saved channel identity')
        return row

    def _admit_cleanup(self,lease,row):
        for owner in (row['owner'],row['cleanup_actor']):
            if owner is not None and owner!=lease.owner:
                require(owner_status(owner)=='dead','The recorded channel owner is alive or its exit cannot be verified')

    def prepare_cleanup(self,lease,ident,session):
        self._lease(lease);value=self._read();row=self._find(value,ident)
        require((row['origin'],row['ship'],row['session_ref'])==(session['url'],session['ship'],session_ref(session)),'Channel cleanup requires its exact saved session')
        if row['phase']=='closed':self._sync();return copy.deepcopy(row)
        self._admit_cleanup(lease,row)
        if row['phase']=='open':row['phase']='closing';row['cleanup_request']=uuid.uuid4().hex
        if row['cleanup_actor']!=lease.owner:
            row['cleanup_actor']=copy.deepcopy(lease.owner);self._write(value)
        else:self._sync()
        return copy.deepcopy(row)

    def deleted(self,lease,ident,request,status):
        self._lease(lease);token(request);require(type(status) is int and status in (200,204),'Exact successful channel deletion response is required')
        value=self._read();row=self._find(value,ident)
        require(row['cleanup_request']==request,'Channel cleanup request changed')
        if row['phase']=='closed':self._sync();return copy.deepcopy(row)
        require(row['phase']=='closing' and row['cleanup_actor']==lease.owner,'Only the admitted cleanup actor may record its response')
        row['phase']='closed';row['delete_status']=status;self._write(value)
        return copy.deepcopy(row)

    def invalidated(self,lease,ident,request,status):
        """An exact channel response proves this session is no longer authorized.

        Keep the denial status, never convert it to a successful delete receipt.
        Application send/receipt journals are deliberately untouched.
        """
        self._lease(lease);token(request)
        require(type(status) is int and status in (401,403),'Exact authorization denial is required')
        value=self._read();row=self._find(value,ident)
        require(row['phase']=='closing' and row['cleanup_request']==request and row['cleanup_actor']==lease.owner,'Only the admitted cleanup actor may record invalidation')
        row['phase']='closed';row['delete_status']=status;self._write(value)
        return copy.deepcopy(row)
