"""Pico APK Grabber - download APKs of apps you own from the PICO Store.

Signs in to your PICO account, searches the store, checks which apps your
account owns, and downloads the official APK (MD5-verified) for those apps.
The PICO server only issues download links for apps your account owns, and
this tool only requests links for apps PICO reports as owned.

It can also install the APK on a headset connected by USB, using ADB (Android
platform-tools). If ADB isn't installed, it offers to download Google's official
platform-tools.

Standard library only (tkinter + urllib), so it packages cleanly with
auto-py-to-exe / PyInstaller.

Protocol based on:
  https://github.com/aeongdesu/pico-steamlink-apk
  https://github.com/nkanf-dev/pico-store-lab
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

APP_NAME = "Pico APK Grabber"
APP_VERSION = "1.1.0"

# ---------------------------------------------------------------------------
# PICO protocol (international store)
# ---------------------------------------------------------------------------

STORE_HOST = "https://appstore-us.picoxr.com"
ACCOUNT_HOST = "https://matrix-us.picovr.com"
WEB_STORE = "https://store-global.picoxr.com/global/detail/1/"
LANGUAGE = "en"
STORE_QUERY = {
    "manifest_version_code": "401200000",
    "device_name": "A9210",
    "app_id": "314431",
    "app_language": LANGUAGE,
    "client_type": "1",
    "zone_name": "America/New_York",
}
PASSPORT_QUERY = {
    "multi_login": "1",
    "account_sdk_source": "app",
    "passport-sdk-version": "30490",
    "aid": "308733",
    "device_platform": "android",
}
USER_AGENT = "okhttp/4.9.3"
ALLOW_HTTP_DOWNLOADS = False  # tests only

ID_RE = re.compile(r"^[0-9]{1,20}$")
PKG_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")


class PicoError(Exception):
    """A readable error from PICO or from this tool."""


class SessionExpired(PicoError):
    """The saved sign-in is no longer valid."""


class Cancelled(PicoError):
    """The user cancelled a download."""


@dataclass
class Session:
    uid: str = "0"
    token: str = ""
    cookies: dict[str, str] = field(default_factory=dict)
    email: str = ""

    def valid(self) -> bool:
        return bool(self.token or self.cookies)

    def headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self.token:
            h["X-Tt-Token"] = self.token
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        return h

    def to_dict(self) -> dict:
        return {"uid": self.uid, "token": self.token, "cookies": self.cookies, "email": self.email}

    @classmethod
    def from_dict(cls, d: dict) -> Session:
        cookies = {str(k): str(v) for k, v in (d.get("cookies") or {}).items() if v}
        return cls(str(d.get("uid") or "0"), str(d.get("token") or ""), cookies,
                   str(d.get("email") or ""))


@dataclass
class StoreApp:
    item_id: str
    package: str
    name: str
    price: str = ""
    currency: str = ""
    version_code: int | None = None
    owned: bool | None = None          # None = not checked yet
    offer_exists: bool | None = None

    @property
    def is_free(self) -> bool:
        return re.fullmatch(r"\s*0+(?:\.0+)?\s*", self.price or "") is not None

    @property
    def store_url(self) -> str:
        return WEB_STORE + self.item_id

    def status_text(self) -> str:
        if self.owned is None:
            return "checking..."
        if self.owned:
            return "Owned"
        if self.offer_exists is False:
            return "Not offered in your region"
        if self.is_free:
            return "Free - not in library"
        return "Not owned"

    def to_dict(self) -> dict:
        return {"item_id": self.item_id, "package": self.package, "name": self.name,
                "price": self.price, "currency": self.currency}


@dataclass
class DownloadFile:
    name: str
    url: str
    md5: str
    size: int


@dataclass
class DownloadInfo:
    version: str
    version_code: int
    apk: DownloadFile
    extras: list[DownloadFile]


def encode_field(value: str) -> str:
    """PICO passport encodes form fields as UTF-8 XOR 0x05, hex."""
    return "".join(f"{b ^ 5:02x}" for b in value.encode("utf-8"))


class PicoAPI:
    def __init__(self, session: Session | None = None) -> None:
        self.session = session or Session()

    # -- transport --------------------------------------------------------
    def _request(self, url: str, *, body: str | None = None, headers: dict | None = None,
                 retries: int = 3):
        hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
        data = body.encode("utf-8") if body is not None else None
        last: Exception | None = None
        for attempt in range(retries):
            req = Request(url, data=data, headers=hdrs, method="POST" if data is not None else "GET")
            try:
                with urlopen(req, timeout=30) as resp:
                    raw = resp.read(8 * 1024 * 1024)
                    try:
                        return json.loads(raw.decode("utf-8") or "{}"), resp.headers
                    except ValueError as e:
                        raise PicoError(f"PICO sent a non-JSON reply (HTTP {resp.status})") from e
            except HTTPError as e:
                if e.code == 401:
                    raise SessionExpired("Your PICO sign-in has expired. Sign in again.") from e
                if e.code < 500 and e.code != 429:
                    raise PicoError(f"PICO returned HTTP {e.code}") from e
                last = e
            except URLError as e:
                last = e
            if attempt + 1 < retries:
                time.sleep(attempt + 1)
        raise PicoError(f"Could not reach PICO: {getattr(last, 'reason', last)}")

    def _store_url(self, path: str) -> str:
        q = dict(STORE_QUERY, uid=self.session.uid or "0", timestamp=str(int(time.time())))
        return f"{STORE_HOST}{path}?{urlencode(q)}"

    def _store_post(self, path: str, body: str, auth: bool, retries: int = 3) -> dict:
        headers = {"Content-Type": "application/json", "Locale": LANGUAGE, "sdk-version": "2"}
        if auth:
            if not self.session.valid():
                raise PicoError("Sign in first.")
            headers.update(self.session.headers())
        data, _ = self._request(self._store_url(path), body=body, headers=headers, retries=retries)
        if not isinstance(data, dict):
            raise PicoError("Unexpected reply from PICO store")
        code = data.get("code")
        if code != 0:
            msg = str(data.get("msg") or data.get("message") or "").strip()
            hint = (" - you may not own this app, or your sign-in expired (try signing in again)"
                    if code in (110001, 10006, 10008) and auth else "")
            raise PicoError(f"PICO store error {code}: {msg or 'no details'}{hint}")
        return data.get("data") or {}

    # -- account ----------------------------------------------------------
    def _passport_post(self, path: str, fields: dict) -> tuple[dict, object]:
        url = f"{ACCOUNT_HOST}{path}?{urlencode(PASSPORT_QUERY)}"
        data, headers = self._request(
            url, body=urlencode(fields),
            headers={"Content-Type": "application/x-www-form-urlencoded"}, retries=1)
        if not isinstance(data, dict) or data.get("message") != "success":
            inner = data.get("data") if isinstance(data, dict) else None
            code = inner.get("error_code") if isinstance(inner, dict) else None
            if code is None and isinstance(data, dict):
                code = data.get("error_code")
            desc = ""
            if isinstance(inner, dict):
                desc = str(inner.get("description") or "")
            hints = {
                1009: "Email or password is incorrect. If you normally sign in with an email "
                      "code, use the code option instead (or set a password on PICO's site).",
                7: "Too many requests. Wait a minute and try again.",
                1104: "PICO wants extra verification. Sign in once on PICO's website or "
                      "headset, then try again.",
                1105: "PICO wants extra verification. Sign in once on PICO's website or "
                      "headset, then try again.",
                1202: "The verification code is wrong or expired.",
                1203: "The verification code is wrong or expired.",
            }
            raise PicoError(hints.get(code, f"PICO sign-in failed (code {code}) {desc}".strip()))
        return data.get("data") or {}, headers

    def _session_from_login(self, data: dict, headers, email: str) -> Session:
        cookies: dict[str, str] = {}
        for line in headers.get_all("Set-Cookie") or []:
            k, sep, v = line.split(";", 1)[0].partition("=")
            if sep and k.strip() and v.strip():
                cookies[k.strip()] = v.strip()
        token = str(headers.get("x-tt-token") or "")
        uid = str(data.get("user_id_str") or data.get("user_id") or "0")
        s = Session(uid, token, cookies, email)
        if not s.valid():
            raise PicoError("PICO accepted the sign-in but returned no session.")
        self.session = s
        if uid in ("", "0"):
            s.uid = self.account_uid()
        return s

    def login_password(self, email: str, password: str) -> Session:
        data, headers = self._passport_post("/passport/user/login/", {
            "mix_mode": "1", "email": encode_field(email), "password": encode_field(password)})
        return self._session_from_login(data, headers, email)

    def send_code(self, email: str) -> None:
        self._passport_post("/passport/email/send_code/", {
            "email": encode_field(email), "type": encode_field("13"),
            "email_logic_type": "0", "mix_mode": "1"})

    def login_code(self, email: str, code: str) -> Session:
        data, headers = self._passport_post("/passport/app/email/code_login/", {
            "email": encode_field(email), "ect_type": "13", "code": encode_field(code),
            "mix_mode": "1", "email_logic_type": "0"})
        return self._session_from_login(data, headers, email)

    def account_uid(self) -> str:
        q = {k: PASSPORT_QUERY[k] for k in ("aid", "device_platform", "passport-sdk-version")}
        data, _ = self._request(f"{ACCOUNT_HOST}/passport/account/info/v2/?{urlencode(q)}",
                                headers=self.session.headers())
        if not isinstance(data, dict) or data.get("message") != "success":
            raise SessionExpired("Your PICO sign-in has expired. Sign in again.")
        d = data.get("data") or {}
        uid = str(d.get("user_id_str") or d.get("user_id") or "")
        if not uid:
            raise SessionExpired("PICO did not return your account id. Sign in again.")
        return uid

    def verify_session(self) -> None:
        self.session.uid = self.account_uid()

    # -- store ------------------------------------------------------------
    def search(self, word: str, next_id: int = 1) -> tuple[list[StoreApp], int | None]:
        body = json.dumps({"word": word.strip()[:100], "pageable": {"next_id": next_id, "size": 20}})
        data = self._store_post("/api/app/v2/search/aggregation", body, auth=False)
        apps: list[StoreApp] = []
        seen: set[str] = set()
        more: int | None = None
        for group in data.get("search_list") or []:
            if not isinstance(group, dict):
                continue
            for it in group.get("items") or []:
                if not isinstance(it, dict):
                    continue
                iid, pkg = str(it.get("item_id", "")), it.get("package_name")
                if not isinstance(pkg, str) or not ID_RE.match(iid) or not PKG_RE.match(pkg):
                    continue  # bundles and non-installable entries
                if iid in seen:
                    continue
                seen.add(iid)
                vc = it.get("version_code")
                apps.append(StoreApp(iid, pkg, str(it.get("name") or pkg),
                                     "" if it.get("price") is None else str(it.get("price")),
                                     version_code=vc if isinstance(vc, int) else None))
            cur = group.get("next_id")
            if group.get("has_more") and isinstance(cur, int) and cur > 0 and more is None:
                more = cur
        return apps, more

    def item(self, package: str) -> StoreApp:
        """Item details; with a session this includes whether you own it."""
        if not PKG_RE.match(package):
            raise PicoError(f"Not a valid package name: {package}")
        auth = self.session.valid()
        d = self._store_post("/api/app/v1/item/info", json.dumps({"package_name": package}), auth)
        if d.get("package_name") != package:
            raise PicoError(f"PICO has no app with package {package}")
        ent = d.get("entitlement_status")
        offer = d.get("is_offer_exist")
        vc = d.get("version_code")
        return StoreApp(
            str(d.get("item_id")), package, str(d.get("name") or package),
            "" if d.get("price") is None else str(d.get("price")),
            "" if d.get("currency") is None else str(d.get("currency")),
            vc if isinstance(vc, int) else None,
            (ent == 1) if (auth and isinstance(ent, int)) else None,
            offer if isinstance(offer, bool) else None)

    def add_free_app(self, app: StoreApp) -> None:
        """'Get' a free app so it is added to your PICO library (same as the store button)."""
        if not app.is_free or not app.currency:
            raise PicoError(f"{app.name} is not a free app.")
        body = (f'{{"item_id":{app.item_id},"is_free_entitlment":true,'
                f'"currency":{json.dumps(app.currency)},"amount":{json.dumps(app.price)},'
                '"support_cross_pay":false}')
        d = self._store_post("/api/app/v1/item/price", body, auth=True, retries=1)
        if d.get("free") is not True:
            raise PicoError("PICO did not confirm the free order.")

    def download_info(self, app: StoreApp, beta: bool = False) -> DownloadInfo:
        if not ID_RE.match(app.item_id) or not PKG_RE.match(app.package):
            raise PicoError("Invalid app id")
        # item_id is larger than a JS-safe int; Python ints are exact, but keep it a raw number.
        body = (f'{{"item_id":{app.item_id},"package_name":{json.dumps(app.package)},'
                f'"incl_dev_preview":{"true" if beta else "false"}}}')
        d = self._store_post("/api/app/v1/download/info", body, auth=True)
        pkg = d.get("package")
        if not isinstance(pkg, dict) or pkg.get("package_name", app.package) != app.package:
            raise PicoError("PICO returned no APK for this app.")
        apk = _file_from(pkg, fallback_name=f"{app.package}.apk")
        if apk is None:
            raise PicoError("PICO returned incomplete APK details.")
        vc = pkg.get("version_code")
        extras = [f for f in _find_files(d) if f.url != apk.url]
        return DownloadInfo(str(pkg.get("version") or ""), vc if isinstance(vc, int) else 0,
                            apk, extras)


def _url_ok(url: object) -> bool:
    return isinstance(url, str) and (url.startswith("https://") or
                                     (ALLOW_HTTP_DOWNLOADS and url.startswith("http://")))


def _file_from(d: dict, fallback_name: str = "") -> DownloadFile | None:
    url = d.get("path") or d.get("url") or d.get("download_url")
    md5 = d.get("md5")
    if not _url_ok(url) or not isinstance(md5, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", md5):
        return None
    size = d.get("size")
    name = (d.get("file_name") or d.get("name") or fallback_name
            or os.path.basename(urlparse(url).path) or "file.bin")
    return DownloadFile(str(name), url, md5.lower(), size if isinstance(size, int) else 0)


def _find_files(node: object, depth: int = 0) -> list[DownloadFile]:
    """Find any other downloadable files (e.g. OBB/expansion data) in download info."""
    out: list[DownloadFile] = []
    if depth > 6:
        return out
    if isinstance(node, dict):
        f = _file_from(node)
        if f:
            out.append(f)
        for v in node.values():
            out.extend(_find_files(v, depth + 1))
    elif isinstance(node, list):
        for v in node:
            out.extend(_find_files(v, depth + 1))
    return out


def safe_filename(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" ._")
    return text[:120] or "app"


def download_file(f: DownloadFile, dest: Path, progress=None, cancel: threading.Event | None = None,
                  retries: int = 3) -> Path:
    """Stream to a .part file, verify MD5, then rename into place."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last: Exception | None = None
    for attempt in range(retries):
        try:
            md5 = hashlib.md5()
            done = 0
            req = Request(f.url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=60) as resp, open(part, "wb") as out:
                total = f.size or int(resp.headers.get("Content-Length") or 0)
                while True:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled("Download cancelled.")
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    md5.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
            if md5.hexdigest() != f.md5:
                raise PicoError(f"Checksum mismatch for {dest.name}")
            os.replace(part, dest)
            return dest
        except Cancelled:
            part.unlink(missing_ok=True)
            raise
        except (OSError, URLError, PicoError) as e:
            last = e
            part.unlink(missing_ok=True)
            if attempt + 1 < retries:
                time.sleep(attempt + 1)
    raise PicoError(f"Download failed: {last}")


# ---------------------------------------------------------------------------
# ADB (install on a headset connected by USB)
# ---------------------------------------------------------------------------

PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-{os}.zip"
PLATFORM_TOOLS_PAGE = "https://developer.android.com/tools/releases/platform-tools"
ADB_EXE = "adb.exe" if sys.platform == "win32" else "adb"

INSTALL_HINTS = {
    "INSTALL_FAILED_UPDATE_INCOMPATIBLE":
        "a copy signed differently is already installed - uninstall it from the headset first",
    "INSTALL_FAILED_VERSION_DOWNGRADE": "a newer version is already installed on the headset",
    "INSTALL_FAILED_INSUFFICIENT_STORAGE": "not enough free space on the headset",
    "INSTALL_FAILED_OLDER_SDK": "the app needs a newer Android version than the headset has",
    "INSTALL_FAILED_NO_MATCHING_ABIS": "the app isn't built for this headset's processor",
    "INSTALL_FAILED_USER_RESTRICTED": "the headset blocked the install - allow it on the headset",
    "INSTALL_FAILED_VERIFICATION_FAILURE": "the headset rejected the app (verification failed)",
}


@dataclass
class AdbDevice:
    serial: str
    state: str
    model: str = ""

    def label(self) -> str:
        return f"{self.model or 'Android device'} ({self.serial})"


def platform_tools_dir() -> Path:
    return data_dir() / "platform-tools"


def find_adb() -> str | None:
    """ADB downloaded by this app first, then any adb on PATH."""
    local = platform_tools_dir() / ADB_EXE
    if local.is_file():
        return str(local)
    return shutil.which("adb")


def download_platform_tools(progress=None, cancel: threading.Event | None = None) -> str:
    """Download and unpack Google's platform-tools into the app's data folder."""
    osname = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    url = PLATFORM_TOOLS_URL.format(os=osname)
    if not (url.startswith("https://") or (ALLOW_HTTP_DOWNLOADS and url.startswith("http://"))):
        raise PicoError("Refusing a non-HTTPS download")
    zpath = data_dir() / "platform-tools.zip.part"
    try:
        with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=60) as resp, \
                open(zpath, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled("Download cancelled.")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        dest_root = data_dir().resolve()
        tmp = data_dir() / "platform-tools.new"
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(zpath) as z:
            for m in z.infolist():
                name = m.filename
                if not name.startswith("platform-tools/") or ".." in Path(name).parts \
                        or Path(name).is_absolute():
                    continue  # only extract the expected folder, never outside it
                target = (tmp / Path(name).relative_to("platform-tools")).resolve()
                if not str(target).startswith(str(tmp.resolve())):
                    continue
                if m.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(m) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                mode = (m.external_attr >> 16) & 0o777
                if os.name != "nt":
                    os.chmod(target, (mode | 0o600) if mode else 0o644)
        exe = tmp / ADB_EXE
        if not exe.is_file():
            shutil.rmtree(tmp, ignore_errors=True)
            raise PicoError("The platform-tools download didn't contain adb.")
        if os.name != "nt":
            os.chmod(exe, 0o755)
        final = platform_tools_dir()
        assert final.resolve().parent == dest_root
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp, final)
        return str(final / ADB_EXE)
    except zipfile.BadZipFile as e:
        raise PicoError("The platform-tools download was damaged. Try again.") from e
    except (OSError, URLError) as e:
        raise PicoError(f"Couldn't download platform-tools: {e}") from e
    finally:
        zpath.unlink(missing_ok=True)


class Adb:
    def __init__(self, path: str) -> None:
        self.path = path
        self._started = False

    def _kw(self) -> dict:
        kw: dict = {"stdin": subprocess.DEVNULL}
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        return kw

    def start_server(self) -> None:
        # Started without pipes so the background server can't keep our pipes open.
        if self._started:
            return
        try:
            subprocess.run([self.path, "start-server"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60, **self._kw())
        except subprocess.TimeoutExpired as e:
            raise PicoError("ADB took too long to start.") from e
        except OSError as e:
            raise PicoError(f"Couldn't run adb ({self.path}): {e}") from e
        self._started = True

    def run(self, *args: str, timeout: int = 60) -> tuple[int, str]:
        self.start_server()
        try:
            r = subprocess.run([self.path, *args], capture_output=True, text=True,
                               errors="replace", timeout=timeout, **self._kw())
        except subprocess.TimeoutExpired as e:
            raise PicoError(f"adb {args[-1] if args else ''} timed out.") from e
        except OSError as e:
            raise PicoError(f"Couldn't run adb ({self.path}): {e}") from e
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def devices(self) -> list[AdbDevice]:
        code, out = self.run("devices", "-l")
        if code != 0:
            raise PicoError(f"adb devices failed: {out.strip()[:200]}")
        found = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 2 or line.startswith(("List of", "*")):
                continue
            model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), "")
            found.append(AdbDevice(parts[0], parts[1], model.replace("_", " ")))
        return found

    def ready_devices(self) -> list[AdbDevice]:
        devs = self.devices()
        ready = [d for d in devs if d.state == "device"]
        if ready:
            return ready
        if any(d.state == "unauthorized" for d in devs):
            raise PicoError("The headset hasn't allowed this computer yet. Put the headset on "
                            "and accept the 'Allow USB debugging' prompt, then try again.")
        if devs:
            raise PicoError(f"The headset isn't ready (state: {devs[0].state}). "
                            "Reconnect the USB cable and try again.")
        raise PicoError("No headset found. Connect it with a USB data cable and turn on USB "
                        "debugging (see the README), then try again.")

    def install(self, serial: str, apk: Path) -> None:
        code, out = self.run("-s", serial, "install", "-r", str(apk), timeout=1800)
        if code == 0 and "Success" in out:
            return
        m = re.search(r"(INSTALL_[A-Z_]+)", out)
        if m:
            hint = INSTALL_HINTS.get(m.group(1))
            raise PicoError(f"install failed: {m.group(1)}" + (f" ({hint})" if hint else ""))
        raise PicoError(f"install failed: {out.strip()[-300:] or f'adb exit code {code}'}")

    def push_obb(self, serial: str, package: str, file: Path) -> str:
        if not PKG_RE.match(package):
            raise PicoError("Invalid package name")
        folder = f"/sdcard/Android/obb/{package}"
        code, out = self.run("-s", serial, "shell", "mkdir", "-p", folder)
        if code != 0:
            raise PicoError(f"couldn't create {folder}: {out.strip()[:200]}")
        remote = f"{folder}/{file.name}"
        code, out = self.run("-s", serial, "push", str(file), remote, timeout=7200)
        if code != 0:
            raise PicoError(f"couldn't copy {file.name} to the headset: {out.strip()[-200:]}")
        return remote


# ---------------------------------------------------------------------------
# Local storage
# ---------------------------------------------------------------------------

def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
        d = base / "PicoApkGrabber"
    else:
        d = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "pico-apk-grabber"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(name: str, default):
    try:
        return json.loads((data_dir() / name).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def save_json(name: str, value) -> None:
    p = data_dir() / name
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2), "utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def delete_json(name: str) -> None:
    (data_dir() / name).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class SignInDialog(tk.Toplevel):
        def __init__(self, app: App) -> None:
            super().__init__(app.root)
            self.app = app
            self.title("Sign in to PICO")
            self.resizable(False, False)
            self.transient(app.root)
            self.grab_set()
            f = ttk.Frame(self, padding=14)
            f.pack(fill="both", expand=True)
            ttk.Label(f, text="Use your PICO international account.",
                      foreground="#555").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
            ttk.Label(f, text="Email").grid(row=1, column=0, sticky="w")
            self.email = ttk.Entry(f, width=34)
            self.email.grid(row=1, column=1, columnspan=2, sticky="we", pady=2)
            self.email.insert(0, app.settings.get("email", ""))

            ttk.Separator(f).grid(row=2, column=0, columnspan=3, sticky="we", pady=8)
            ttk.Label(f, text="Password").grid(row=3, column=0, sticky="w")
            self.password = ttk.Entry(f, width=24, show="•")
            self.password.grid(row=3, column=1, sticky="we", pady=2)
            self.pw_btn = ttk.Button(f, text="Sign in", command=self.with_password)
            self.pw_btn.grid(row=3, column=2, padx=(6, 0))

            ttk.Label(f, text="— or use an email code —", foreground="#777").grid(
                row=4, column=0, columnspan=3, pady=8)
            self.code_send = ttk.Button(f, text="Send code to email", command=self.send_code)
            self.code_send.grid(row=5, column=1, sticky="w")
            ttk.Label(f, text="Code").grid(row=6, column=0, sticky="w")
            self.code = ttk.Entry(f, width=24)
            self.code.grid(row=6, column=1, sticky="we", pady=2)
            self.code_btn = ttk.Button(f, text="Sign in", command=self.with_code)
            self.code_btn.grid(row=6, column=2, padx=(6, 0))

            self.remember = tk.BooleanVar(value=app.settings.get("remember", True))
            ttk.Checkbutton(f, text="Keep me signed in on this computer",
                            variable=self.remember).grid(row=7, column=0, columnspan=3,
                                                         sticky="w", pady=(10, 0))
            self.msg = ttk.Label(f, text="", foreground="#b00", wraplength=330)
            self.msg.grid(row=8, column=0, columnspan=3, sticky="w", pady=(6, 0))
            self.password.bind("<Return>", lambda e: self.with_password())
            self.code.bind("<Return>", lambda e: self.with_code())
            (self.password if self.email.get() else self.email).focus_set()

        def _busy(self, busy: bool, text: str = "") -> None:
            for b in (self.pw_btn, self.code_btn, self.code_send):
                b.configure(state="disabled" if busy else "normal")
            self.msg.configure(text=text, foreground="#555" if busy else "#b00")

        def _email(self) -> str | None:
            e = self.email.get().strip()
            if not re.fullmatch(r"\S+@\S+\.\S+", e):
                self.msg.configure(text="Enter a valid email address.", foreground="#b00")
                return None
            return e

        def with_password(self) -> None:
            email, pw = self._email(), self.password.get()
            if not email or not pw:
                return
            self._busy(True, "Signing in...")
            self.app.bg(lambda: PicoAPI().login_password(email, pw), self._done, self._fail)

        def send_code(self) -> None:
            email = self._email()
            if not email:
                return
            self._busy(True, "Sending code...")

            def ok(_):
                self._busy(False)
                self.msg.configure(text=f"Code sent to {email}. Check your inbox.",
                                   foreground="#060")
                self.code.focus_set()
            self.app.bg(lambda: PicoAPI().send_code(email), ok, self._fail)

        def with_code(self) -> None:
            email, code = self._email(), self.code.get().strip()
            if not email or not code:
                return
            self._busy(True, "Signing in...")
            self.app.bg(lambda: PicoAPI().login_code(email, code), self._done, self._fail)

        def _done(self, session: Session) -> None:
            self.app.settings["email"] = session.email
            self.app.settings["remember"] = self.remember.get()
            self.app.set_session(session, remember=self.remember.get())
            self.destroy()

        def _fail(self, err: Exception) -> None:
            self._busy(False)
            self.msg.configure(text=str(err), foreground="#b00")

    class App:
        COLS = ("name", "package", "price", "status")

        def __init__(self, root: tk.Tk) -> None:
            self.root = root
            self.api = PicoAPI()
            self.settings: dict = load_json("settings.json", {})
            self.library: dict[str, dict] = load_json("library.json", {})
            self.results: dict[str, StoreApp] = {}
            self.next_id: int | None = None
            self.last_query = ""
            self.cancel = threading.Event()
            self.busy = False
            self.q: queue.Queue = queue.Queue()
            self.check_gen = 0
            self.adb: Adb | None = None

            root.title(f"{APP_NAME} {APP_VERSION}")
            root.geometry("920x720")
            root.minsize(820, 640)
            self._build()
            root.after(50, self._pump)
            saved = load_json("session.json", None)
            if saved:
                s = Session.from_dict(saved)
                if s.valid():
                    self.account_lbl.configure(text="Checking saved sign-in...")
                    self.api.session = s
                    def bad(e):
                        if isinstance(e, SessionExpired):
                            self.log("Saved sign-in expired. Please sign in again.")
                            self.sign_out(quiet=True)
                        else:
                            self.log(f"Couldn't verify saved sign-in ({e}); keeping it.")
                            self._refresh_account()
                    self.bg(lambda: (self.api.verify_session(), s)[1],
                            lambda s: self.set_session(s, remember=True), bad)
            else:
                self._refresh_account()
            self._fill_library()

        # -- layout ---------------------------------------------------------
        def _build(self) -> None:
            root = self.root
            top = ttk.Frame(root, padding=(10, 8))
            top.pack(fill="x")
            self.account_lbl = ttk.Label(top, text="Not signed in", font=("TkDefaultFont", 10, "bold"))
            self.account_lbl.pack(side="left")
            self.signout_btn = ttk.Button(top, text="Sign out", command=self.sign_out)
            self.signout_btn.pack(side="right")
            self.signin_btn = ttk.Button(top, text="Sign in", command=lambda: SignInDialog(self))
            self.signin_btn.pack(side="right", padx=4)

            self.tabs = ttk.Notebook(root)
            self.tabs.pack(fill="both", expand=True, padx=10)

            # Store tab
            st = ttk.Frame(self.tabs, padding=8)
            self.tabs.add(st, text="Search store")
            bar = ttk.Frame(st)
            bar.pack(fill="x")
            self.search_var = tk.StringVar()
            e = ttk.Entry(bar, textvariable=self.search_var)
            e.pack(side="left", fill="x", expand=True)
            e.bind("<Return>", lambda _e: self.search())
            ttk.Button(bar, text="Search", command=self.search).pack(side="left", padx=4)
            self.more_btn = ttk.Button(bar, text="More results", command=self.more,
                                       state="disabled")
            self.more_btn.pack(side="left")
            self.owned_only = tk.BooleanVar(value=False)
            ttk.Checkbutton(bar, text="Owned only", variable=self.owned_only,
                            command=self._refill_results).pack(side="left", padx=8)
            ttk.Label(st, text="Search by app name, or type an exact package name "
                               "(e.g. com.valvesoftware.steamlinkvr).",
                      foreground="#666").pack(anchor="w", pady=(4, 4))
            self.tree = self._make_tree(st)

            # Library tab
            lt = ttk.Frame(self.tabs, padding=8)
            self.tabs.add(lt, text="My library")
            ttk.Label(lt, text="Apps found as owned are saved here so you can grab updates later. "
                              "PICO has no public 'list my library' call, so search for an app "
                              "once to add it.", foreground="#666", wraplength=820,
                      justify="left").pack(anchor="w", pady=(0, 4))
            lb = ttk.Frame(lt)
            lb.pack(fill="x", pady=(0, 4))
            ttk.Button(lb, text="Download all", command=self.download_all).pack(side="left")
            ttk.Button(lb, text="Remove from list", command=self.remove_from_library).pack(
                side="left", padx=4)
            self.lib_tree = self._make_tree(lt)

            # Bottom
            bot = ttk.Frame(root, padding=(10, 6))
            bot.pack(fill="x")
            ttk.Label(bot, text="Save to").pack(side="left")
            default_out = str(Path.home() / "Downloads" / "Pico APKs")
            self.out_var = tk.StringVar(value=self.settings.get("output", default_out))
            ttk.Entry(bot, textvariable=self.out_var).pack(side="left", fill="x", expand=True,
                                                           padx=4)
            ttk.Button(bot, text="Browse...", command=self.browse).pack(side="left")
            ttk.Button(bot, text="Open folder", command=self.open_folder).pack(side="left", padx=4)

            act = ttk.Frame(root, padding=(10, 0))
            act.pack(fill="x")
            self.dl_btn = ttk.Button(act, text="Download selected", command=self.download_selected)
            self.dl_btn.pack(side="left")
            self.inst_btn = ttk.Button(act, text="Download + install on headset (USB)",
                                       command=self.download_install)
            self.inst_btn.pack(side="left", padx=(4, 0))
            self.cancel_btn = ttk.Button(act, text="Cancel", command=self.cancel.set,
                                         state="disabled")
            self.cancel_btn.pack(side="right")

            act2 = ttk.Frame(root, padding=(10, 6, 10, 0))
            act2.pack(fill="x")
            self.free_btn = ttk.Button(act2, text="Add free app to my library",
                                       command=self.add_free)
            self.free_btn.pack(side="left")
            ttk.Button(act2, text="Open store page", command=self.open_store).pack(side="left",
                                                                                padx=4)
            self.beta = tk.BooleanVar(value=self.settings.get("beta", False))
            ttk.Checkbutton(act2, text="Prefer beta / preview build if available",
                            variable=self.beta).pack(side="left", padx=10)

            hs = ttk.Frame(root, padding=(10, 6, 10, 0))
            hs.pack(fill="x")
            ttk.Label(hs, text="Headset:").pack(side="left")
            self.hs_lbl = ttk.Label(hs, text="not checked - connect it by USB to install directly",
                                    foreground="#666")
            self.hs_lbl.pack(side="left", padx=4)
            ttk.Button(hs, text="Check headset", command=self.check_headset).pack(side="right")

            pf = ttk.Frame(root, padding=(10, 6))
            pf.pack(fill="x")
            self.progress = ttk.Progressbar(pf, mode="determinate", maximum=1000)
            self.progress.pack(fill="x")
            self.prog_lbl = ttk.Label(pf, text="")
            self.prog_lbl.pack(anchor="w")

            lf = ttk.Frame(root, padding=(10, 0, 10, 10))
            lf.pack(fill="both")
            self.logbox = tk.Text(lf, height=7, wrap="word", state="disabled",
                                  font=("TkFixedFont", 9))
            self.logbox.pack(fill="both", expand=True)

        def _make_tree(self, parent):
            frame = ttk.Frame(parent)
            frame.pack(fill="both", expand=True)
            tree = ttk.Treeview(frame, columns=self.COLS, show="headings", selectmode="extended")
            for col, text, w in (("name", "Name", 260), ("package", "Package", 260),
                                 ("price", "Price", 80), ("status", "Status", 170)):
                tree.heading(col, text=text)
                tree.column(col, width=w, anchor="w")
            sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            tree.tag_configure("owned", foreground="#0a7a20")
            tree.tag_configure("free", foreground="#1a5fb4")
            tree.tag_configure("locked", foreground="#888")
            tree.bind("<Double-1>", lambda _e: self.download_selected())
            return tree

        # -- background work ----------------------------------------------
        def bg(self, fn, ok=None, fail=None) -> None:
            def work():
                try:
                    r = fn()
                    self.q.put((ok, r, None))
                except Exception as e:  # noqa: BLE001
                    if not isinstance(e, PicoError):
                        traceback.print_exc()
                    self.q.put((fail, None, e))
            threading.Thread(target=work, daemon=True).start()

        def ui(self, fn, *args) -> None:
            self.q.put((lambda _r: fn(*args), None, None))

        def _pump(self) -> None:
            try:
                while True:
                    ok, r, err = self.q.get_nowait()
                    if err is not None:
                        if ok:  # ok is the fail handler here
                            ok(err)
                        else:
                            self.error(err)
                    elif ok:
                        ok(r)
            except queue.Empty:
                pass
            self.root.after(50, self._pump)

        def log(self, text: str) -> None:
            self.logbox.configure(state="normal")
            self.logbox.insert("end", time.strftime("%H:%M:%S  ") + text + "\n")
            self.logbox.see("end")
            self.logbox.configure(state="disabled")

        def error(self, err: Exception) -> None:
            self.log(f"Error: {err}")
            if isinstance(err, SessionExpired):
                self.sign_out(quiet=True)
                messagebox.showwarning(APP_NAME, str(err))

        # -- account --------------------------------------------------------
        def set_session(self, s: Session, remember: bool) -> None:
            self.api.session = s
            if remember:
                save_json("session.json", s.to_dict())
            else:
                delete_json("session.json")
            save_json("settings.json", self.settings)
            self.log(f"Signed in as {s.email or 'PICO user'}.")
            self._refresh_account()
            self.recheck_results()
            self.recheck_library()

        def sign_out(self, quiet: bool = False) -> None:
            self.api.session = Session()
            delete_json("session.json")
            if not quiet:
                self.log("Signed out.")
            for a in self.results.values():
                a.owned = None
            self._refill_results()
            self._refresh_account()

        def _refresh_account(self) -> None:
            s = self.api.session
            if s.valid():
                self.account_lbl.configure(text=f"Signed in: {s.email or 'PICO account'}")
                self.signout_btn.configure(state="normal")
                self.signin_btn.configure(state="disabled")
            else:
                self.signin_btn.configure(state="normal")
                self.account_lbl.configure(text="Not signed in - sign in to see what you own")
                self.signout_btn.configure(state="disabled")

        # -- search ---------------------------------------------------------
        def search(self) -> None:
            word = self.search_var.get().strip()
            if not word:
                return
            self.last_query = word
            self.results.clear()
            self.next_id = None
            self._refill_results()
            self.log(f"Searching for '{word}'...")

            def work():
                apps: list[StoreApp] = []
                if PKG_RE.match(word) and " " not in word:
                    try:
                        apps.append(self.api.item(word))
                    except PicoError:
                        pass
                found, nxt = self.api.search(word)
                return apps + found, nxt
            self.bg(work, self._got_results)

        def more(self) -> None:
            if self.next_id is None:
                return
            nid, word = self.next_id, self.last_query
            self.bg(lambda: self.api.search(word, nid), self._got_results)

        def _got_results(self, r) -> None:
            apps, nxt = r
            self.next_id = nxt
            self.more_btn.configure(state="normal" if nxt else "disabled")
            new = []
            for a in apps:
                if a.item_id not in self.results:
                    self.results[a.item_id] = a
                    new.append(a)
            self.log(f"{len(self.results)} app(s) found.")
            self._refill_results()
            self._check_ownership(new)

        def recheck_results(self) -> None:
            self._check_ownership(list(self.results.values()))

        def recheck_library(self) -> None:
            apps = [StoreApp(d["item_id"], d["package"], d["name"], d.get("price", ""),
                             d.get("currency", "")) for d in self.library.values()]
            self._check_ownership(apps, library=True)

        def _check_ownership(self, apps: list[StoreApp], library: bool = False) -> None:
            if not apps:
                return
            if not library:
                self.check_gen += 1
            gen = self.check_gen
            signed_in = self.api.session.valid()

            def work():
                owned = 0
                for a in apps:
                    if not library and gen != self.check_gen:
                        return
                    try:
                        d = self.api.item(a.package)
                    except SessionExpired:
                        raise
                    except PicoError as e:
                        self.ui(self.log, f"{a.name}: {e}")
                        continue
                    self.ui(self._apply_item, a, d, library)
                    if d.owned:
                        owned += 1
                    time.sleep(0.15)
                if signed_in and not library:
                    self.ui(self.log, f"Ownership checked: {owned} owned.")
            self.bg(work)

        def _apply_item(self, a: StoreApp, d: StoreApp, from_library: bool) -> None:
            a.price, a.currency = d.price, d.currency
            a.owned, a.offer_exists = d.owned, d.offer_exists
            a.version_code = d.version_code
            self._update_app(a, from_library)

        def _update_app(self, a: StoreApp, from_library: bool) -> None:
            if a.owned and a.item_id not in self.library:
                self.library[a.item_id] = a.to_dict()
                save_json("library.json", self.library)
                self.log(f"Added to My library: {a.name}")
            if a.owned and a.item_id in self.library:
                self.library[a.item_id].update(a.to_dict())
            if a.item_id in self.results and not from_library:
                self.results[a.item_id] = a
            self._refill_results()
            self._fill_library(updated=a)

        def _row(self, a: StoreApp):
            price = "Free" if a.is_free else (f"{a.price} {a.currency}".strip() or "-")
            status = a.status_text() if self.api.session.valid() else "sign in to check"
            tag = "owned" if a.owned else ("free" if a.is_free else "locked")
            return (a.name, a.package, price, status), (tag,)

        def _refill_results(self) -> None:
            self.tree.delete(*self.tree.get_children())
            for a in self.results.values():
                if self.owned_only.get() and not a.owned:
                    continue
                vals, tags = self._row(a)
                self.tree.insert("", "end", iid=a.item_id, values=vals, tags=tags)

        def _fill_library(self, updated: StoreApp | None = None) -> None:
            if updated is not None:
                if self.lib_tree.exists(updated.item_id):
                    vals, tags = self._row(updated)
                    self.lib_tree.item(updated.item_id, values=vals, tags=tags)
                    return
                if updated.item_id not in self.library:
                    return
            self.lib_tree.delete(*self.lib_tree.get_children())
            for d in sorted(self.library.values(), key=lambda d: d["name"].lower()):
                a = StoreApp(d["item_id"], d["package"], d["name"], d.get("price", ""),
                             d.get("currency", ""), owned=True)
                vals, tags = self._row(a)
                if not self.api.session.valid():
                    vals = (*vals[:3], "saved")
                self.lib_tree.insert("", "end", iid=a.item_id, values=vals, tags=tags)

        def remove_from_library(self) -> None:
            for iid in self.lib_tree.selection():
                self.library.pop(iid, None)
            save_json("library.json", self.library)
            self._fill_library()

        # -- selection helpers ---------------------------------------------
        def _selected(self) -> list[StoreApp]:
            if self.tabs.index("current") == 1:
                out = []
                for iid in self.lib_tree.selection():
                    d = self.library.get(iid)
                    if d:
                        out.append(StoreApp(d["item_id"], d["package"], d["name"],
                                            d.get("price", ""), d.get("currency", ""), owned=True))
                return out
            return [self.results[i] for i in self.tree.selection() if i in self.results]

        def open_store(self) -> None:
            for a in self._selected()[:3]:
                webbrowser.open(a.store_url)

        def browse(self) -> None:
            d = filedialog.askdirectory(initialdir=self.out_var.get() or str(Path.home()))
            if d:
                self.out_var.set(d)

        def open_folder(self) -> None:
            p = Path(self.out_var.get())
            p.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(p)  # type: ignore[attr-defined]
            else:
                webbrowser.open(p.as_uri())

        # -- actions --------------------------------------------------------
        def add_free(self) -> None:
            if not self._need_session():
                return
            apps = [a for a in self._selected() if a.is_free and not a.owned]
            if not apps:
                messagebox.showinfo(APP_NAME, "Select a free app that isn't in your library yet.")
                return
            names = "\n".join(a.name for a in apps)
            if not messagebox.askyesno(APP_NAME, f"Add these free apps to your PICO library?\n\n"
                                                 f"{names}"):
                return

            def work():
                for a in apps:
                    self.api.add_free_app(a)
                    self.ui(self.log, f"Added free app to your PICO library: {a.name}")
                return apps
            self.bg(work, lambda apps: self._check_ownership(apps))

        def download_selected(self) -> None:
            self._start_downloads(self._selected())

        def download_all(self) -> None:
            self.tabs.select(1)
            self.lib_tree.selection_set(self.lib_tree.get_children())
            self._start_downloads(self._selected(), skip_existing=True)

        # -- headset (ADB) ----------------------------------------------------
        def _with_adb(self, then) -> None:
            """Find adb (offering to download Google's platform-tools), then call then(adb)."""
            path = self.settings.get("adb_path") or find_adb()
            if path and (Path(path).is_file() or shutil.which(path)):
                if self.adb is None or self.adb.path != path:
                    self.adb = Adb(path)
                then(self.adb)
                return
            if not messagebox.askyesno(
                    APP_NAME,
                    "Installing on a headset needs ADB (Google's Android platform-tools), which "
                    "wasn't found on this computer.\n\nDownload it now from Google (about 7 MB)? "
                    f"It's covered by Google's terms:\n{PLATFORM_TOOLS_PAGE}\n\n"
                    "Choose No to just download APKs instead."):
                return
            if self.busy:
                messagebox.showinfo(APP_NAME, "Wait for the current download to finish.")
                return
            self.busy = True
            self.cancel.clear()
            self.cancel_btn.configure(state="normal")
            self.log("Downloading Android platform-tools from Google...")

            def prog(done, total):
                self.ui(self._set_progress, "platform-tools", done, total)

            def ok(p):
                self._finish()
                self.log(f"ADB ready: {p}")
                self.adb = Adb(p)
                then(self.adb)

            def fail(e):
                self._finish()
                self.error(e)
            self.bg(lambda: download_platform_tools(prog, self.cancel), ok, fail)

        def _pick_device(self, devs: list[AdbDevice]) -> AdbDevice | None:
            if len(devs) == 1:
                return devs[0]
            from tkinter import simpledialog
            lines = "\n".join(f"{i + 1}. {d.label()}" for i, d in enumerate(devs))
            n = simpledialog.askinteger(APP_NAME, f"Several devices are connected:\n\n{lines}"
                                                  "\n\nInstall on which one? (number)",
                                        minvalue=1, maxvalue=len(devs), parent=self.root)
            return devs[n - 1] if n else None

        def check_headset(self) -> None:
            def go(adb: Adb):
                self.hs_lbl.configure(text="checking...", foreground="#666")

                def ok(devs):
                    names = ", ".join(d.label() for d in devs)
                    self.hs_lbl.configure(text=f"connected: {names}", foreground="#0a7a20")
                    self.log(f"Headset connected: {names}")

                def fail(e):
                    self.hs_lbl.configure(text="not ready - see the log", foreground="#b00")
                    self.error(e)
                self.bg(adb.ready_devices, ok, fail)
            self._with_adb(go)

        def download_install(self) -> None:
            apps = self._selected()
            if not apps:
                messagebox.showinfo(APP_NAME, "Select one or more apps first.")
                return
            if self.busy:
                messagebox.showinfo(APP_NAME, "A download is already running.")
                return
            if not self._need_session():
                return

            def go(adb: Adb):
                def ok(devs):
                    d = self._pick_device(devs)
                    if d is None:
                        return
                    self.hs_lbl.configure(text=f"connected: {d.label()}", foreground="#0a7a20")
                    self._start_downloads(apps, install_on=d)

                def fail(e):
                    self.hs_lbl.configure(text="not ready - see the log", foreground="#b00")
                    self.error(e)
                    messagebox.showwarning(APP_NAME, str(e))
                self.bg(adb.ready_devices, ok, fail)
            self._with_adb(go)

        def _need_session(self) -> bool:
            if self.api.session.valid():
                return True
            messagebox.showinfo(APP_NAME, "Sign in to your PICO account first.")
            SignInDialog(self)
            return False

        def _start_downloads(self, apps: list[StoreApp], skip_existing: bool = False,
                             install_on: AdbDevice | None = None) -> None:
            if self.busy:
                messagebox.showinfo(APP_NAME, "A download is already running.")
                return
            if not apps:
                messagebox.showinfo(APP_NAME, "Select one or more apps first.")
                return
            if not self._need_session():
                return
            out = Path(self.out_var.get()).expanduser()
            self.settings["output"] = str(out)
            self.settings["beta"] = self.beta.get()
            save_json("settings.json", self.settings)
            beta = self.beta.get()
            self.busy = True
            self.cancel.clear()
            self.cancel_btn.configure(state="normal")
            self.dl_btn.configure(state="disabled")
            self.inst_btn.configure(state="disabled")
            adb = self.adb if install_on else None

            def progress(name):
                last = [0.0]

                def cb(done, total):
                    now = time.time()
                    if now - last[0] < 0.1 and done != total:
                        return
                    last[0] = now
                    self.ui(self._set_progress, name, done, total)
                return cb

            def work():
                ok, failed, installed = 0, [], 0
                for a in apps:
                    if self.cancel.is_set():
                        break
                    try:
                        # Always confirm ownership with PICO before asking for a link.
                        cur = self.api.item(a.package)
                        if not cur.owned:
                            why = ("free but not in your library - use 'Add free app' first"
                                   if cur.is_free else "your account doesn't own it")
                            raise PicoError(f"skipped, {why}")
                        info = self.api.download_info(cur, beta)
                        stem = safe_filename(f"{cur.name}_v{info.version}_{info.version_code}")
                        dest = out / f"{stem}.apk"
                        if dest.exists() and skip_existing and not adb:
                            self.ui(self.log, f"{cur.name}: already have {dest.name}")
                            ok += 1
                            continue
                        mb = info.apk.size / 1048576 if info.apk.size else 0
                        self.ui(self.log, f"{cur.name}: downloading v{info.version} "
                                          f"({info.version_code}){f', {mb:.1f} MB' if mb else ''}")
                        download_file(info.apk, dest, progress(cur.name), self.cancel)
                        self.ui(self.log, f"{cur.name}: saved {dest.name} (MD5 verified)")
                        extras = []
                        for x in info.extras:
                            xdest = out / f"{stem}_files" / safe_filename(x.name)
                            self.ui(self.log, f"{cur.name}: extra data file {x.name}")
                            download_file(x, xdest, progress(x.name), self.cancel)
                            extras.append(xdest)
                        ok += 1
                        if adb and install_on:
                            try:
                                self.ui(self.log, f"{cur.name}: installing on "
                                                  f"{install_on.label()}...")
                                self.ui(self._set_progress_text, f"Installing {cur.name}...")
                                adb.install(install_on.serial, dest)
                                for xdest in extras:
                                    self.ui(self.log, f"{cur.name}: copying {xdest.name} "
                                                      "to the headset...")
                                    remote = adb.push_obb(install_on.serial, cur.package, xdest)
                                    self.ui(self.log, f"{cur.name}: copied to {remote}")
                                installed += 1
                                self.ui(self.log, f"{cur.name}: installed on the headset")
                            except PicoError as e:
                                failed.append(a.name)
                                self.ui(self.log, f"{cur.name}: saved the APK, but {e}")
                    except Cancelled:
                        self.ui(self.log, "Cancelled.")
                        break
                    except SessionExpired:
                        raise
                    except PicoError as e:
                        failed.append(a.name)
                        self.ui(self.log, f"{a.name}: {e}")
                return ok, failed, installed

            def done(r):
                ok, failed, installed = r
                self._finish()
                msg = f"Done: {ok} downloaded"
                if adb:
                    msg += f", {installed} installed on the headset"
                if failed:
                    msg += f", {len(failed)} with problems (see above)"
                self.log(msg)

            def fail(e):
                self._finish()
                self.error(e)
            self.bg(work, done, fail)

        def _set_progress(self, name: str, done: int, total: int) -> None:
            if total:
                self.progress.configure(value=done * 1000 / total)
                self.prog_lbl.configure(
                    text=f"{name}: {done / 1048576:.1f} / {total / 1048576:.1f} MB")
            else:
                self.prog_lbl.configure(text=f"{name}: {done / 1048576:.1f} MB")

        def _set_progress_text(self, text: str) -> None:
            self.progress.configure(value=0)
            self.prog_lbl.configure(text=text)

        def _finish(self) -> None:
            self.busy = False
            self.cancel_btn.configure(state="disabled")
            self.dl_btn.configure(state="normal")
            self.inst_btn.configure(state="normal")
            self.progress.configure(value=0)
            self.prog_lbl.configure(text="")

    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
