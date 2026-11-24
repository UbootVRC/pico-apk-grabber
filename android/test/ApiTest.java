import com.picoapkgrabber.PicoApi;
import com.picoapkgrabber.PicoApi.*;

import java.io.File;
import java.nio.file.Files;
import java.util.concurrent.atomic.AtomicBoolean;

/** Runs PicoApi against the fake server in tests/test_grabber.py (see run_android_api_test.py). */
public class ApiTest {
    static void check(boolean ok, String what) {
        if (!ok) throw new AssertionError(what);
    }

    static void expectFail(Runnable r, String contains) {
        try {
            r.run();
        } catch (RuntimeException e) {
            if (e.getCause() instanceof PicoException && e.getCause().getMessage().contains(contains)) return;
            throw new AssertionError("wrong error: " + e.getCause());
        }
        throw new AssertionError("expected failure containing " + contains);
    }

    interface Call { void run() throws Exception; }

    static Runnable wrap(final Call c) {
        return new Runnable() {
            public void run() {
                try {
                    c.run();
                } catch (Exception e) {
                    throw new RuntimeException(e);
                }
            }
        };
    }

    public static void main(String[] args) throws Exception {
        String host = args[0];
        final String big = args[1];
        PicoApi.STORE_HOST = host;
        PicoApi.ACCOUNT_HOST = host;
        PicoApi.allowHttpDownloads = true;

        final PicoApi api = new PicoApi();
        expectFail(wrap(new Call() { public void run() throws Exception { api.loginPassword("me@x.com", "bad"); } }),
                "incorrect");
        Session s = api.loginPassword("me@x.com", "pw");
        check("555".equals(s.uid) && "abc".equals(s.cookies.get("sessionid")), "password login " + s.toJson());
        api.verifySession();

        PicoApi api2 = new PicoApi();
        api2.sendCode("me@x.com");
        Session s2 = api2.loginCode("me@x.com", "123456");
        check("tok123".equals(s2.token), "code login");
        check("tok123".equals(Session.fromJson(s2.toJson()).token), "session roundtrip");

        Session bogus = new Session();
        bogus.cookies.put("sessionid", "zzz");
        try {
            new PicoApi(bogus).verifySession();
            throw new AssertionError("expected expiry");
        } catch (SessionExpired expected) {
        }

        SearchPage page = new PicoApi().search("game", 1);
        check(page.apps.size() == 3 && page.nextId == null, "search size " + page.apps.size());
        boolean foundBig = false;
        for (StoreApp a : page.apps) foundBig |= a.itemId.equals(big);
        check(foundBig, "big id exact");

        check(new PicoApi().item("com.paid.owned").owned == null, "unauth owned unknown");
        StoreApp owned = api.item("com.paid.owned");
        final StoreApp locked = api.item("com.paid.notowned");
        final StoreApp free = api.item("com.free.game");
        check(owned.owned && !locked.owned && !free.owned, "ownership");
        check(owned.itemId.equals(big), "item id " + owned.itemId);
        check("Free - not in library".equals(free.statusText()) && "Not owned".equals(locked.statusText()), "status");

        DownloadInfo info = api.downloadInfo(owned, false);
        check(info.versionCode == 42 && info.extras.size() == 1, "download info");
        check(api.downloadInfo(owned, true).versionCode == 43, "beta");
        File tmp = Files.createTempDirectory("dl").toFile();
        final long[] seen = {0, 0};
        File apk = PicoApi.download(info.apk, new File(tmp, "a.apk"), new Progress() {
            public void update(long d, long t) { seen[0] = d; seen[1] = t; }
        }, new AtomicBoolean(false), 3);
        check(apk.length() == 300000 && seen[0] == 300000 && seen[1] == 300000, "apk download");
        check(PicoApi.download(info.extras.get(0), new File(tmp, "x/main.obb"), null, null, 1).length() == 50000, "obb");

        expectFail(wrap(new Call() { public void run() throws Exception { api.downloadInfo(free, false); } }), "10006");
        expectFail(wrap(new Call() { public void run() throws Exception { api.addFreeApp(locked); } }), "not a free");
        api.addFreeApp(free);
        check(api.item("com.free.game").owned, "free added");
        check("Bad_Name_Here__v1.0".equals(PicoApi.safeName("Bad:Name/Here?_v1.0")), "safeName");
        System.out.println("Android API tests passed");
    }
}
