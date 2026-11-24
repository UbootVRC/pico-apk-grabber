#!/usr/bin/env python3
"""Stand-in for adb used by the tests (Linux/macOS). Behaviour comes from a JSON file
named in FAKE_ADB_STATE; every call is appended to FAKE_ADB_LOG."""
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_ADB_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
state = json.load(open(os.environ["FAKE_ADB_STATE"]))

if args == ["start-server"]:
    sys.exit(0)
if args[:1] == ["devices"]:
    print("List of devices attached")
    for serial, st, model in state.get("devices", []):
        print(f"{serial}\t{st} usb:1-1 product:x model:{model} device:x transport_id:1")
    print()
    sys.exit(0)
if args[:1] == ["-s"]:
    serial, cmd = args[1], args[2:]
    if cmd[:1] == ["install"]:
        apk = cmd[-1]
        if not os.path.isfile(apk):
            print(f"adb: failed to stat {apk}", file=sys.stderr)
            sys.exit(1)
        res = state.get("install", "Success")
        print("Performing Streamed Install")
        if res == "Success":
            print("Success")
            sys.exit(0)
        print(f"adb: failed to install {apk}: Failure [{res}: details]", file=sys.stderr)
        sys.exit(1)
    if cmd[:2] == ["shell", "mkdir"]:
        sys.exit(0)
    if cmd[:1] == ["push"]:
        src, dst = cmd[1], cmd[2]
        if not os.path.isfile(src):
            sys.exit(1)
        print(f"{src}: 1 file pushed, 0 skipped. 12.3 MB/s ({os.path.getsize(src)} bytes in 0.01s)")
        sys.exit(0)
print("unknown command", args, file=sys.stderr)
sys.exit(1)
