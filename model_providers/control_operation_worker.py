"""One-shot control POST with credentials and payload supplied through stdin."""
import json
import sys
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    try:
        config = json.loads(sys.stdin.buffer.read(8193))
        request = Request(config["url"], data=json.dumps(config["payload"], ensure_ascii=False).encode(), method="POST",
            headers={"Authorization": "Bearer " + config["token"], "Content-Type": "application/json",
                     "Accept": "application/json"})
        with build_opener(ProxyHandler({}), NoRedirects()).open(request, timeout=18) as response:
            if (response.status != 200 or response.headers.get_content_type() != "application/json"
                    or response.headers.get("Content-Encoding")):
                return 1
            raw = response.read(65537)
            if len(raw) > 65536 or response.length not in (None, 0):
                return 1
        sys.stdout.buffer.write(raw)
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
