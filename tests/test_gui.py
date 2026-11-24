"""Drive the real tkinter GUI against the fake server (run under xvfb)."""
import gc
import json
import os
import subprocess
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

cfg = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = cfg
sys.path.insert(0, os.path.dirname(__file__))
import test_grabber as t  # noqa: E402

t.main()  # starts fake server, patches hosts, runs API tests
g = t.g
t.owned_now.discard("com.free.game")
(Path(cfg) / "pico-apk-grabber").mkdir(parents=True, exist_ok=True)
(Path(cfg) / "pico-apk-grabber" / "session.json").write_text(json.dumps(
    {"uid": "555", "cookies": {"sessionid": "abc"}, "email": "me@x.com"}))
out = Path(tempfile.mkdtemp())
messagebox.askyesno = lambda *a, **k: True
messagebox.showinfo = lambda *a, **k: print("INFO:", a[1:])
messagebox.showwarning = lambda *a, **k: print("WARN:", a[1:])

steps = []


def app():
    return next(o for o in gc.get_objects() if type(o).__name__ == "App")


def wait(cond, then, tries=200):
    a = app()
    if cond(a):
        then(a)
    elif tries == 0:
        print("TIMEOUT; log:\n" + a.logbox.get("1.0", "end")); a.root.destroy(); sys.exit(1)
    else:
        a.root.after(50, lambda: wait(cond, then, tries - 1))


def shot(name):
    try:  # optional screenshot (ImageMagick on Linux)
        subprocess.run(["import", "-window", "root",
                        os.path.join(os.path.dirname(__file__), f"{name}.png")], check=False)
    except OSError:
        pass


def s1(a):
    print("account:", a.account_lbl.cget("text"))
    a.search_var.set("game")
    a.search()
    wait(lambda a: len(a.results) == 3 and all(x.owned is not None for x in a.results.values()), s2)


def s2(a):
    rows = {a.tree.set(i, "name"): a.tree.set(i, "status") for i in a.tree.get_children()}
    print("rows:", rows)
    assert rows == {"Owned Paid Game": "Owned", "Locked Game": "Not owned",
                    "Free Game": "Free - not in library"}, rows
    assert list(a.library) == [str(t.BIG)], a.library
    a.root.update(); shot("gui_search")
    a.out_var.set(str(out))
    a.tree.selection_set([str(t.BIG), "1000000000000000001"])
    a.download_selected()
    wait(lambda a: not a.busy, s3)


def s3(a):
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*"))
    print("files:", files)
    assert "Owned Paid Game_v1.2.3_42.apk" in files, files
    assert "Owned Paid Game_v1.2.3_42_files/main.42.obb" in files, files
    log = a.logbox.get("1.0", "end")
    assert "Locked Game: skipped" in log, log
    a.tree.selection_set(["2000000000000000002"])
    a.add_free()
    wait(lambda a: a.results["2000000000000000002"].owned is True and len(a.library) == 2, s4)


def s4(a):
    a.tabs.select(1)
    a.root.update()
    lib = {a.lib_tree.set(i, "name"): a.lib_tree.set(i, "status") for i in a.lib_tree.get_children()}
    print("library:", lib)
    assert lib == {"Owned Paid Game": "Owned", "Free Game": "Owned"}, lib
    a.download_all()
    wait(lambda a: not a.busy, s5)


def s5(a):
    a.root.update(); shot("gui_library")
    print(a.logbox.get("1.0", "end"))
    assert "already have" in a.logbox.get("1.0", "end")
    a.sign_out()
    assert not (Path(cfg) / "pico-apk-grabber" / "session.json").exists()
    print("GUI tests passed")
    a.root.destroy()


def fake_mainloop(self, n=0):
    wait(lambda a: a.account_lbl.cget("text").startswith("Signed in"), s1)
    tk.Misc.mainloop(self, n)


tk.Tk.mainloop = fake_mainloop
g.run_gui()
