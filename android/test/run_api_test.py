"""Start the fake PICO server from ../../tests and run ApiTest.java against it on a desktop JVM."""
import os
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "..", "tests"))
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
import test_grabber as t  # noqa: E402

srv = ThreadingHTTPServer(("127.0.0.1", 0), t.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
t.owned_now.discard("com.free.game")

json_jar = "/usr/share/java/com.android.json.jar"  # apt: libandroid-json-java
out = tempfile.mkdtemp()
src = [os.path.join(here, "..", "src", "com", "picoapkgrabber", "PicoApi.java"),
       os.path.join(here, "ApiTest.java")]
subprocess.run(["javac", "-nowarn", "-cp", json_jar, "-d", out, *src], check=True)
r = subprocess.run(["java", "-Dhttp.nonProxyHosts=127.0.0.1", "-cp", f"{out}:{json_jar}", "ApiTest",
                    f"http://127.0.0.1:{srv.server_port}", str(t.BIG)])
sys.exit(r.returncode)
