"""Direct session lifecycle helpers: no proxy use or redirect following."""

import http.client
import ssl
import urllib.request
from urllib.parse import urlsplit

from session_store import StoreError


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def revoke(session):
    url = urlsplit(session['url'])
    for method, path in (('GET', '/~/host'), ('POST', '/~/logout')):
        cls = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
        options = {'context': ssl.create_default_context()} if url.scheme == 'https' else {}
        conn = cls(url.hostname, url.port, timeout=5, **options)
        headers = {'Accept-Encoding': 'identity'}
        if method == 'POST':
            headers['Cookie'] = session['cookieName'] + '=' + session['cookieValue']
        try:
            conn.request(method, path, body=b'' if method == 'POST' else None, headers=headers)
            response = conn.getresponse()
            if method == 'GET':
                if response.status != 200 or response.read(257).strip() != session['ship'].encode():
                    raise StoreError('Ship identity changed; logout was not confirmed')
            elif response.status in (200, 204, 303):
                return 'revoked'
            elif response.status in (401, 403):
                return 'rejected'  # Not a claim that a successful logout was observed.
            else:
                raise StoreError('Remote logout was not confirmed; the saved session is retained for retry')
        except (OSError, http.client.HTTPException):
            raise StoreError('Remote logout was not confirmed; the saved session is retained for retry') from None
        finally:
            conn.close()
