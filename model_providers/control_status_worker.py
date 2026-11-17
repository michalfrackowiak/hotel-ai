"""One-shot stdlib transport; input and output are private bounded pipes."""
import json
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    try:
        config = json.loads(sys.stdin.buffer.read(1025))
        request = Request(config["url"], headers={"Authorization": "Bearer " + config["token"],
                                                "Accept": "application/json"})
        with build_opener(ProxyHandler({}), NoRedirects()).open(request, timeout=5) as response:
            if (response.status != 200 or response.headers.get_content_type() != "application/json"
                    or response.headers.get("Content-Encoding")):
                return 2
            raw = response.read(65537)
            if len(raw) > 65536 or response.length not in (None, 0):
                return 2
        sys.stdout.buffer.write(raw)
        return 0
    except HTTPError as error:
        return {429: 3, 401: 4, 403: 4}.get(error.code, 5)
    except Exception:
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
