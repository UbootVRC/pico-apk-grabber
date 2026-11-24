"""Tests for installing on a headset over ADB, using tests/fake_adb.py (Linux/macOS).

Covers: finding adb, device states, install success/failure, OBB copy, the
platform-tools download (from a local fake), and the GUI 'Download + install' flow.
"""
import io
import json
import os
import stat
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

here = Path(__file__).resolve().parent
cfg = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = cfg
sys.path.insert(0, str(here))
import test_grabber as t  # noqa: E402

g = t.g
work = Path(tempfile.mkdtemp())
log_file, state_file = work / "adb.log", work / "state.json"
os.environ["FAKE_ADB_LOG"], os.environ["FAKE_ADB_STATE"] = str(log_file), str(state_file)
fake_dir = work / "bin"
fake_dir.mkdir()
fake = fake_dir / "adb"
fake.write_text(f"#!{sys.executable}\n" + (here / "fake_adb.py").read_text())
fake.chmod(0o755)


def set_state(**kw):
    state_file.write_text(json.dumps(kw))
    log_file.write_text("")


def calls():
    return [json.loads(x) for x in log_file.read_text().splitlines()]


def expect_error(fn, contains):
    try:
        fn()
    except g.PicoError as e:
        assert contains in str(e), e
        return
    raise AssertionError(f"expected error containing {contains!r}")


def test_core():
    os.environ["PATH"] = os.pathsep.join(["/usr/bin", "/bin"])  # no adb
    assert g.find_adb() is None
    os.environ["PATH"] = str(fake_dir) + os.pathsep + os.environ["PATH"]
    assert g.find_adb() == str(fake)
    adb = g.Adb(g.find_adb())

    set_state(devices=[])
    expect_error(adb.ready_devices, "No headset found")
    set_state(devices=[["ABC", "unauthorized", "x"]])
    expect_error(adb.ready_devices, "Allow USB debugging")
    set_state(devices=[["ABC", "offline", "x"]])
    expect_error(adb.ready_devices, "isn't ready")
    set_state(devices=[["PA7L10MGF", "device", "A8150"], ["OTHER", "unauthorized", "y"]])
    devs = adb.ready_devices()
    assert [(d.serial, d.model) for d in devs] == [("PA7L10MGF", "A8150")], devs
    assert ["start-server"] not in calls()  # server started once per Adb, before this log
    g.Adb(str(fake)).devices()
    assert calls()[-2:] == [["start-server"], ["devices", "-l"]], calls()

    apk = work / "My App_v1.apk"
    apk.write_bytes(b"x" * 10)
    adb.install("PA7L10MGF", apk)
    assert calls()[-1] == ["-s", "PA7L10MGF", "install", "-r", str(apk)], calls()
    set_state(install="INSTALL_FAILED_UPDATE_INCOMPATIBLE")
    expect_error(lambda: adb.install("S", apk), "uninstall it from the headset first")
    set_state(install="INSTALL_FAILED_SOMETHING_NEW")
    expect_error(lambda: adb.install("S", apk), "INSTALL_FAILED_SOMETHING_NEW")

    obb = work / "main.42.com.paid.owned.obb"
    obb.write_bytes(b"o" * 5)
    set_state()
    remote = adb.push_obb("S", "com.paid.owned", obb)
    assert remote == "/sdcard/Android/obb/com.paid.owned/main.42.com.paid.owned.obb"
    assert calls()[-2:] == [["-s", "S", "shell", "mkdir", "-p", "/sdcard/Android/obb/com.paid.owned"],
                            ["-s", "S", "push", str(obb), remote]], calls()
    expect_error(lambda: adb.push_obb("S", "bad;rm -rf", obb), "Invalid package")
    print("ADB core tests passed")


def test_platform_tools(host_port):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("platform-tools/adb")
        info.external_attr = (stat.S_IFREG | 0o755) << 16
        z.writestr(info, f"#!{sys.executable}\n" + (here / "fake_adb.py").read_text())
        z.writestr("platform-tools/NOTICE.txt", "notice")
        z.writestr("../escape.txt", "nope")
        z.writestr("platform-tools/../../escape2.txt", "nope")
    payload = buf.getvalue()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    g.PLATFORM_TOOLS_URL = f"http://127.0.0.1:{srv.server_port}/pt-{{os}}.zip"
    seen = []
    path = g.download_platform_tools(lambda d, tot: seen.append((d, tot)))
    assert path == str(g.platform_tools_dir() / "adb") and os.access(path, os.X_OK), path
    assert seen[-1] == (len(payload), len(payload))
    assert not list(Path(cfg).rglob("escape*")), "zip entries escaped the folder"
    assert g.find_adb() == path  # downloaded copy is preferred
    set_state(devices=[["S1", "device", "Headset"]])
    assert g.Adb(path).ready_devices()[0].serial == "S1"
    print("platform-tools download tests passed")


def test_gui():
    import gc
    import tkinter as tk
    from tkinter import messagebox

    t.owned_now.discard("com.free.game")
    (Path(cfg) / "pico-apk-grabber" / "session.json").write_text(json.dumps(
        {"uid": "555", "cookies": {"sessionid": "abc"}, "email": "me@x.com"}))
    out = Path(tempfile.mkdtemp())
    warnings = []
    messagebox.askyesno = lambda *a, **k: True
    messagebox.showinfo = lambda *a, **k: print("INFO:", a[1:])
    messagebox.showwarning = lambda *a, **k: warnings.append(a[1])

    def app():
        return next(o for o in gc.get_objects() if type(o).__name__ == "App")

    def wait(cond, then, tries=300):
        a = app()
        if cond(a):
            then(a)
        elif tries == 0:
            print("TIMEOUT; log:\n" + a.logbox.get("1.0", "end"))
            a.root.destroy()
            sys.exit(1)
        else:
            a.root.after(50, lambda: wait(cond, then, tries - 1))

    def s1(a):
        a.search_var.set("game")
        a.search()
        wait(lambda a: len(a.results) == 3 and all(x.owned is not None for x in a.results.values()), s2)

    def s2(a):
        set_state(devices=[])  # nothing plugged in
        a.out_var.set(str(out))
        a.tree.selection_set([str(t.BIG)])
        a.download_install()
        wait(lambda a: warnings, s3)

    def s3(a):
        assert "No headset found" in warnings[0], warnings
        assert not list(out.iterdir()), "nothing should download without a headset"
        set_state(devices=[["PA7L10MGF", "device", "A8150"]])
        a.download_install()
        wait(lambda a: not a.busy and "installed on the headset" in a.logbox.get("1.0", "end")
             and "Done:" in a.logbox.get("1.0", "end"), s4)

    def s4(a):
        log = a.logbox.get("1.0", "end")
        print(log)
        c = calls()
        installs = [x for x in c if "install" in x]
        pushes = [x for x in c if "push" in x]
        assert len(installs) == 1 and installs[0][-1].endswith("Owned Paid Game_v1.2.3_42.apk"), c
        assert len(pushes) == 1 and pushes[0][-1] == "/sdcard/Android/obb/com.paid.owned/main.42.obb", c
        assert (out / "Owned Paid Game_v1.2.3_42.apk").exists(), "APK is still saved on the PC"
        assert "1 installed on the headset" in log
        assert "connected: A8150" in a.hs_lbl.cget("text")
        set_state(devices=[["PA7L10MGF", "device", "A8150"]], install="INSTALL_FAILED_VERSION_DOWNGRADE")
        a.download_install()
        wait(lambda a: not a.busy and log.count("Done:") < a.logbox.get("1.0", "end").count("Done:"), s5)

    def s5(a):
        log = a.logbox.get("1.0", "end")
        assert "saved the APK, but install failed: INSTALL_FAILED_VERSION_DOWNGRADE" in log, log
        assert "0 installed on the headset, 1 with problems" in log, log
        a.root.update()
        try:
            import subprocess
            subprocess.run(["import", "-window", "root", str(here / "gui_adb.png")], check=False)
        except OSError:
            pass
        print("ADB GUI tests passed")
        a.root.destroy()

    def fake_mainloop(self, n=0):
        wait(lambda a: a.account_lbl.cget("text").startswith("Signed in"), s1)
        tk.Misc.mainloop(self, n)

    tk.Tk.mainloop = fake_mainloop
    g.run_gui()


if __name__ == "__main__":
    t.main()  # fake PICO server + API tests
    test_core()
    test_platform_tools(None)
    test_gui()
