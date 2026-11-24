"""End-to-end tests of pico_apk_grabber against a fake PICO server."""
import hashlib
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ["NO_PROXY"] = "127.0.0.1,localhost"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pico_apk_grabber as g  # noqa: E402

APK = os.urandom(300_000)
OBB = os.urandom(50_000)
BIG = 7494362493653958711  # > 2^53
OWNED = {"com.paid.owned": BIG, "com.free.game": 2000000000000000002}
CATALOG = {
    "com.paid.owned": (BIG, "Owned Paid Game", "19.99"),
    "com.paid.notowned": (1000000000000000001, "Locked Game", "9.99"),
    "com.free.game": (2000000000000000002, "Free Game", "0"),
}
owned_now = {"com.paid.owned"}
log = []


def xor(hexs):
    return bytes(b ^ 5 for b in bytes.fromhex(hexs)).decode()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, headers=()):
        raw = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def authed(self):
        return "sessionid=abc" in (self.headers.get("Cookie") or "") or \
            self.headers.get("X-Tt-Token") == "tok123"

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/files/app.apk":
            self.send_response(200); self.send_header("Content-Length", str(len(APK))); self.end_headers()
            self.wfile.write(APK); return
        if u.path == "/files/main.obb":
            self.send_response(200); self.end_headers(); self.wfile.write(OBB); return
        if u.path == "/passport/account/info/v2/":
            if self.authed():
                return self._json({"message": "success", "data": {"user_id_str": "555"}})
            self.send_response(401); self.end_headers(); return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        u = urlparse(self.path)
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        log.append((u.path, body, dict(self.headers)))
        if u.path == "/passport/user/login/":
            f = parse_qs(body)
            if xor(f["email"][0]) == "me@x.com" and xor(f["password"][0]) == "pw":
                return self._json({"message": "success", "data": {"user_id": 555}},
                                  [("Set-Cookie", "sessionid=abc; Path=/"),
                                   ("Set-Cookie", "sid_tt=abc; Path=/")])
            return self._json({"message": "error", "data": {"error_code": 1009}})
        if u.path == "/passport/email/send_code/":
            return self._json({"message": "success", "data": {}})
        if u.path == "/passport/app/email/code_login/":
            f = parse_qs(body)
            if xor(f["code"][0]) == "123456":
                return self._json({"message": "success", "data": {"user_id_str": "555"}},
                                  [("x-tt-token", "tok123")])
            return self._json({"message": "error", "data": {"error_code": 1202}})
        q = parse_qs(u.query)
        assert q["app_language"] == ["en"], q
        if u.path == "/api/app/v2/search/aggregation":
            j = json.loads(body)
            items = [{"item_id": iid, "package_name": p, "name": n, "price": pr}
                     for p, (iid, n, pr) in CATALOG.items() if j["word"].lower() in n.lower()]
            items.append({"item_id": 9, "name": "A bundle"})  # non-installable
            return self._json({"code": 0, "data": {"search_list": [
                {"items": items, "has_more": False}]}})
        if u.path == "/api/app/v1/item/info":
            p = json.loads(body)["package_name"]
            if p not in CATALOG:
                return self._json({"code": 404, "msg": "nope"})
            iid, n, pr = CATALOG[p]
            d = {"item_id": iid, "package_name": p, "name": n, "price": pr, "currency": "USD",
                 "version_code": 10, "is_offer_exist": True}
            if self.authed():
                d["entitlement_status"] = 1 if p in owned_now else 0
            return self._json({"code": 0, "data": d})
        if u.path == "/api/app/v1/item/price":
            assert self.authed()
            assert f'"item_id":{CATALOG["com.free.game"][0]}' in body, body
            owned_now.add("com.free.game")
            return self._json({"code": 0, "data": {"free": True, "order_id": "77"}})
        if u.path == "/api/app/v1/download/info":
            assert f'"item_id":{BIG}' in body or "com.free.game" in body, body  # exact big int
            j = json.loads(body)
            if not self.authed() or j["package_name"] not in owned_now:
                return self._json({"code": 10006, "msg": "no entitlement"})
            host = f"http://127.0.0.1:{self.server.server_port}"
            return self._json({"code": 0, "data": {
                "item_id": CATALOG[j["package_name"]][0],
                "package": {"package_name": j["package_name"], "version": "1.2.3",
                            "version_code": 42 if not j["incl_dev_preview"] else 43,
                            "size": len(APK), "md5": hashlib.md5(APK).hexdigest(),
                            "path": f"{host}/files/app.apk"},
                "obb_list": [{"file_name": "main.42.obb", "md5": hashlib.md5(OBB).hexdigest(),
                              "size": len(OBB), "path": f"{host}/files/main.obb"}]}})
        self.send_response(404); self.end_headers()


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host = f"http://127.0.0.1:{srv.server_port}"
    g.STORE_HOST = g.ACCOUNT_HOST = host
    g.ALLOW_HTTP_DOWNLOADS = True

    api = g.PicoAPI()
    # wrong password -> readable message
    try:
        api.login_password("me@x.com", "bad"); raise SystemExit("expected failure")
    except g.PicoError as e:
        assert "incorrect" in str(e), e
    s = api.login_password("me@x.com", "pw")
    assert s.uid == "555" and s.cookies["sessionid"] == "abc", s
    api.verify_session()

    api2 = g.PicoAPI()
    api2.send_code("me@x.com")
    s2 = api2.login_code("me@x.com", "123456")
    assert s2.token == "tok123" and s2.uid == "555"
    assert g.Session.from_dict(s2.to_dict()).headers()["X-Tt-Token"] == "tok123"

    # expired session
    try:
        g.PicoAPI(g.Session("1", "", {"sessionid": "zzz"})).verify_session()
        raise SystemExit("expected expiry")
    except g.SessionExpired:
        pass

    apps, nxt = g.PicoAPI().search("game")
    assert {a.package for a in apps} == set(CATALOG) and nxt is None, apps
    assert any(a.item_id == str(BIG) for a in apps), "big id lost precision"

    pub = g.PicoAPI().item("com.paid.owned")
    assert pub.owned is None  # not signed in -> unknown
    owned = api.item("com.paid.owned")
    locked = api.item("com.paid.notowned")
    free = api.item("com.free.game")
    assert owned.owned is True and locked.owned is False and free.owned is False
    assert free.status_text() == "Free - not in library" and locked.status_text() == "Not owned"

    # download info / download
    info = api.download_info(owned)
    assert info.version_code == 42 and len(info.extras) == 1, info
    assert api.download_info(owned, beta=True).version_code == 43
    tmp = Path(tempfile.mkdtemp())
    seen = []
    p = g.download_file(info.apk, tmp / "a.apk", lambda d, t: seen.append((d, t)))
    assert p.read_bytes() == APK and seen[-1] == (len(APK), len(APK))
    x = g.download_file(info.extras[0], tmp / "x" / "main.obb")
    assert x.read_bytes() == OBB
    bad = g.DownloadFile("b.apk", info.apk.url, "0" * 32, 0)
    try:
        g.download_file(bad, tmp / "b.apk", retries=1); raise SystemExit("expected md5 fail")
    except g.PicoError as e:
        assert "Checksum" in str(e)
    assert not (tmp / "b.apk").exists() and not (tmp / "b.apk.part").exists()
    ev = threading.Event(); ev.set()
    try:
        g.download_file(info.apk, tmp / "c.apk", cancel=ev); raise SystemExit("expected cancel")
    except g.Cancelled:
        pass

    # not owned -> store refuses
    try:
        api.download_info(free); raise SystemExit("expected refusal")
    except g.PicoError as e:
        assert "10006" in str(e)

    try:
        api.add_free_app(locked); raise SystemExit("paid add should fail")
    except g.PicoError:
        pass
    api.add_free_app(free)
    assert api.item("com.free.game").owned is True
    assert api.download_info(api.item("com.free.game")).version_code == 42

    assert g.safe_filename('Bad:Name/Here?_v1.0') == "Bad_Name_Here__v1.0"
    print("API tests passed")
    return host


if __name__ == "__main__":
    main()
