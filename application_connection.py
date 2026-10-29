"""Private stdin receiver; normal DM use has no Omabit runtime dependency."""

import json
import signal
import sys

from instance import INSTANCE
from connection_offer import HandoffError, acknowledgement, check_identity, decode, plugin_session
from connection_journal import Journal, fingerprint, reconcile_request, reconciled, secret_present, sync_directory
from session_store import SessionStore


def accept(raw, store=None, instance=INSTANCE, verify=check_identity):
    offer = decode(raw, instance)
    store = store if store is not None else SessionStore(instance=instance)
    verify(offer['session'])
    with store.locked():
        if store.account() is not None:
            raise HandoffError('account-exists')
        session = plugin_session(offer['session'])
        journal = Journal(store.root)
        row = journal.begin(offer, session)
        store._save(session, application_request_id=offer['request_id'])
        row['phase'] = 'accepted'
        journal.save()
    return acknowledgement(offer)


def reconcile(raw, store=None, instance=INSTANCE, present=secret_present):
    value = reconcile_request(raw, instance)
    store = store if store is not None else SessionStore(instance=instance)
    with store.locked():
        journal = Journal(store.root)
        row = journal.find(value)
        if row is None:
            return reconciled(value, 'absent')
        if row['phase'] == 'reconciled':
            return reconciled(value, row['outcome'])
        account = store.account()
        if account is not None and account['url'] == row['origin'] and (account.get('application_request_id') != row['request_id']
                or (account['url'], account['ship']) != (row['origin'], row['ship'])):
            journal.finish(row, 'superseded')
            return reconciled(value, 'superseded')
        if present(instance, row['origin']):
            session = store.lookup(row['origin'], row['ship'], check_expiry=False)
            if fingerprint(session) != row['fingerprint']:
                journal.finish(row, 'superseded')
                return reconciled(value, 'superseded')
            store._secret('clear', row['origin'])
            if present(instance, row['origin']):
                raise HandoffError('keyring-unavailable')
        outcome = 'absent'
        if (account is not None and account.get('application_request_id') == row['request_id']
                and (account['url'], account['ship']) == (row['origin'], row['ship'])):
            (store.root / 'account.json').unlink()
            sync_directory(store.root)
            outcome = 'removed'
        elif account is not None:
            outcome = 'superseded'
        journal.finish(row, outcome)
        return reconciled(value, outcome)


def main():
    def timeout(*_args):
        raise HandoffError('timeout')
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(25)
    try:
        raw = sys.stdin.buffer.read(16385)
        if sys.argv[1:] == ['--reconcile']:
            result = reconcile(raw)
        elif not sys.argv[1:]:
            result = accept(raw)
        else:
            raise HandoffError('invalid')
    except HandoffError as error:
        result = {'version': 1, 'status': 'rejected', 'code': str(error)}
    except Exception:
        result = {'version': 1, 'status': 'rejected', 'code': 'storage'}
    finally:
        signal.alarm(0)
    print(json.dumps(result, separators=(',', ':')), flush=True)
    return 0 if result['status'] in ('accepted', 'reconciled') else 1


if __name__ == '__main__':
    sys.exit(main())
