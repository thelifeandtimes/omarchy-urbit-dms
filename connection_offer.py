"""Version 1 private session offer. No Omabit runtime dependency or data proxy."""

from datetime import datetime, timezone
import http.client
import ipaddress
import json
import re
import ssl
from urllib.parse import urlsplit


class HandoffError(ValueError):
    pass


def decode(raw, instance):
    if not isinstance(raw, (bytes, bytearray, str)) or len(raw) > 16384:
        raise HandoffError('invalid')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise HandoffError('invalid')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        required = {'version', 'request_id', 'plugin_id', 'profile', 'connection_id', 'session'}
        if len(raw) > 16384 or not isinstance(value, dict) or set(value) != required:
            raise ValueError()
        if type(value['version']) is not int or value['version'] != 1:
            raise ValueError()
        for key in ('request_id', 'connection_id'):
            if not isinstance(value[key], str) or not re.fullmatch(r'[0-9a-f]{32}', value[key]):
                raise ValueError()
        if (value['plugin_id'], value['profile']) != (instance.plugin_id, instance.profile):
            raise HandoffError('wrong-recipient')
        session = value['session']
        if (not isinstance(session, dict) or set(session) != {'origin', 'ship', 'cookie_name', 'cookie_value', 'expires'}
                or not all(isinstance(item, str) for item in session.values())):
            raise ValueError()
        url = urlsplit(session['origin'])
        host = url.hostname
        if (url.scheme not in ('https', 'http') or not host or url.username is not None or url.password is not None
                or url.path or url.query or url.fragment or url.port == 0 or len(session['origin']) > 2048
                or any(ord(c) < 33 or ord(c) > 126 or c in '\\%?#' for c in session['origin'])):
            raise ValueError()
        # The owner selected this exact endpoint. LAN/tailnet HTTP is allowed;
        # no redirects or proxy environment are used when verifying its identity.
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
            if (len(host) > 253 or re.fullmatch(r'[0-9.]+', host)
                    or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.'))):
                raise ValueError()
        if address is not None:
            address = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
            if address.is_unspecified or address.is_multicast:
                raise ValueError()
        host = '[' + host + ']' if ':' in host else host
        port = '' if url.port is None else ':' + str(url.port)
        if port == (':443' if url.scheme == 'https' else ':80') or session['origin'] != url.scheme + '://' + host + port:
            raise ValueError()
        if (not re.fullmatch(r'~[a-z]+(?:-+[a-z]+)*', session['ship']) or len(session['ship']) > 128
                or session['cookie_name'] != 'urbauth-' + session['ship']
                or not re.fullmatch(r'[A-Za-z0-9._~-]{1,4096}', session['cookie_value'])):
            raise ValueError()
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})', session['expires']):
            raise ValueError()
        expiry = datetime.fromisoformat(session['expires'])
        remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
        if not 0 < remaining <= 30 * 86400:
            raise HandoffError('expired')
        return value
    except HandoffError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError):
        raise HandoffError('invalid') from None


def check_identity(session):
    """Probe publicly first; send the cookie only to this exact origin, no redirects."""
    url = urlsplit(session['origin'])
    for path, authenticated in (('/~/host', False), ('/~/name', True)):
        cls = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
        options = {'context': ssl.create_default_context()} if url.scheme == 'https' else {}
        conn = cls(url.hostname, url.port, timeout=5, **options)
        headers = {'Accept': 'text/plain', 'Accept-Encoding': 'identity'}
        if authenticated:
            headers['Cookie'] = session['cookie_name'] + '=' + session['cookie_value']
        try:
            conn.request('GET', path, headers=headers)
            response = conn.getresponse()
            if response.status != 200 or response.read(257).strip() != session['ship'].encode():
                raise HandoffError('identity')
        except (OSError, http.client.HTTPException):
            raise HandoffError('identity') from None
        finally:
            conn.close()


def plugin_session(session):
    return {'url': session['origin'], 'ship': session['ship'],
            'cookieName': session['cookie_name'], 'cookieValue': session['cookie_value'],
            'expires': session['expires']}


def acknowledgement(offer):
    return {key: offer[key] for key in ('version', 'request_id', 'connection_id', 'plugin_id', 'profile')} | {'status': 'accepted'}
