"""Small standard-library transport shared by the CLI and Kodi service."""

import base64
import json as json_module
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward API credentials to a redirected host or sign-in page.
        return None


class Response:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        # urlopen already raises for non-success responses.
        return None

    def json(self):
        return json_module.loads(self.content)


class Session:
    def __init__(self, api_key=None, username=None, password=None):
        self.headers = {"Accept": "application/json"}
        if api_key:
            self.headers["X-Api-Key"] = api_key
        if username:
            credentials = (username + ":" + (password or "")).encode("utf-8")
            self.headers["Authorization"] = "Basic " + base64.b64encode(
                credentials
            ).decode("ascii")
        self.opener = build_opener(NoRedirect())

    def request(self, method, url, *, params=None, json=None, timeout=30):
        if params:
            url += "?" + urlencode(params)
        headers = dict(self.headers)
        data = None
        if json is not None:
            data = json_module.dumps(json).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return Response(response.read())
        except HTTPError as error:
            raise RuntimeError(f"API returned HTTP {error.code}") from None
        except URLError:
            raise RuntimeError(
                "API connection failed. Check URL, network and TLS certificate"
            ) from None

    def close(self):
        return None


class KodiSession:
    def __init__(self, execute_json_rpc):
        self.execute_json_rpc = execute_json_rpc

    def request(self, method, url, *, params=None, json=None, timeout=30):
        if method != "POST" or json is None:
            raise ValueError("Kodi internal transport accepts JSON-RPC POST calls only")
        return Response(self.execute_json_rpc(json_module.dumps(json)).encode("utf-8"))

    def close(self):
        return None
