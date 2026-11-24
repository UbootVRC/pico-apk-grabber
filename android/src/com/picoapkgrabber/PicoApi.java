package com.picoapkgrabber;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UnsupportedEncodingException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * PICO Store client (international store). No Android dependencies besides org.json,
 * so it can be tested on a desktop JVM.
 *
 * Protocol from github.com/aeongdesu/pico-steamlink-apk and github.com/nkanf-dev/pico-store-lab.
 */
public final class PicoApi {
    public static String STORE_HOST = "https://appstore-us.picoxr.com";
    public static String ACCOUNT_HOST = "https://matrix-us.picovr.com";
    public static final String WEB_STORE = "https://store-global.picoxr.com/global/detail/1/";
    public static boolean allowHttpDownloads = false; // tests only

    static final String LANG = "en";
    static final String UA = "okhttp/4.9.3";
    static final Pattern ID = Pattern.compile("[0-9]{1,20}");
    static final Pattern PKG = Pattern.compile("[A-Za-z0-9_]+(\\.[A-Za-z0-9_]+)+");
    static final Pattern MD5 = Pattern.compile("[0-9a-fA-F]{32}");
    static final Pattern EMAIL = Pattern.compile("\\S+@\\S+\\.\\S+");
    // 64-bit ids are kept as exact strings (they can exceed a double's precision).
    static final Pattern BIG_ID = Pattern.compile("\"(item_id|order_id|user_id)\"\\s*:\\s*(\\d+)");

    // ------------------------------------------------------------------ errors

    public static class PicoException extends Exception {
        public PicoException(String m) { super(m); }
    }

    public static class SessionExpired extends PicoException {
        public SessionExpired(String m) { super(m); }
    }

    public static class Cancelled extends PicoException {
        public Cancelled() { super("Download cancelled."); }
    }

    // ------------------------------------------------------------------ models

    public static class Session {
        public String uid = "0";
        public String token = "";
        public String email = "";
        public LinkedHashMap<String, String> cookies = new LinkedHashMap<String, String>();

        public boolean valid() { return token.length() > 0 || !cookies.isEmpty(); }

        void apply(HttpURLConnection c) {
            if (token.length() > 0) c.setRequestProperty("X-Tt-Token", token);
            if (!cookies.isEmpty()) {
                StringBuilder sb = new StringBuilder();
                for (Map.Entry<String, String> e : cookies.entrySet()) {
                    if (sb.length() > 0) sb.append("; ");
                    sb.append(e.getKey()).append('=').append(e.getValue());
                }
                c.setRequestProperty("Cookie", sb.toString());
            }
        }

        public String toJson() {
            try {
                JSONObject o = new JSONObject();
                o.put("uid", uid).put("token", token).put("email", email);
                o.put("cookies", new JSONObject(cookies));
                return o.toString();
            } catch (JSONException e) {
                return "{}";
            }
        }

        public static Session fromJson(String s) {
            Session out = new Session();
            if (s == null || s.length() == 0) return out;
            try {
                JSONObject o = new JSONObject(s);
                out.uid = o.optString("uid", "0");
                out.token = o.optString("token", "");
                out.email = o.optString("email", "");
                JSONObject c = o.optJSONObject("cookies");
                if (c != null) {
                    Iterator<String> it = c.keys();
                    while (it.hasNext()) {
                        String k = it.next();
                        String v = c.optString(k, "");
                        if (v.length() > 0) out.cookies.put(k, v);
                    }
                }
            } catch (JSONException ignored) {
            }
            return out;
        }
    }

    public static class StoreApp {
        public String itemId, pkg, name;
        public String price = "", currency = "";
        public int versionCode = 0;
        public Boolean owned = null;       // null = unknown / not checked
        public Boolean offerExists = null;

        public StoreApp(String itemId, String pkg, String name) {
            this.itemId = itemId;
            this.pkg = pkg;
            this.name = name;
        }

        public boolean isFree() { return price != null && price.trim().matches("0+(\\.0+)?"); }

        public String storeUrl() { return WEB_STORE + itemId; }

        public String priceText() {
            if (isFree()) return "Free";
            String p = (price + " " + currency).trim();
            return p.length() == 0 ? "-" : p;
        }

        public String statusText() {
            if (owned == null) return "checking...";
            if (owned) return "Owned";
            if (Boolean.FALSE.equals(offerExists)) return "Not offered in your region";
            if (isFree()) return "Free - not in library";
            return "Not owned";
        }

        public void copyFrom(StoreApp d) {
            price = d.price;
            currency = d.currency;
            owned = d.owned;
            offerExists = d.offerExists;
            versionCode = d.versionCode;
            if (d.name != null && d.name.length() > 0) name = d.name;
        }

        public JSONObject toJson() throws JSONException {
            return new JSONObject().put("item_id", itemId).put("package", pkg).put("name", name)
                    .put("price", price).put("currency", currency);
        }

        public static StoreApp fromJson(JSONObject o) {
            StoreApp a = new StoreApp(o.optString("item_id"), o.optString("package"), o.optString("name"));
            a.price = o.optString("price", "");
            a.currency = o.optString("currency", "");
            return a;
        }
    }

    public static class DownloadFile {
        public final String name, url, md5;
        public final long size;

        DownloadFile(String name, String url, String md5, long size) {
            this.name = name;
            this.url = url;
            this.md5 = md5;
            this.size = size;
        }
    }

    public static class DownloadInfo {
        public String version = "";
        public long versionCode;
        public DownloadFile apk;
        public List<DownloadFile> extras = new ArrayList<DownloadFile>();
    }

    public static class SearchPage {
        public List<StoreApp> apps = new ArrayList<StoreApp>();
        public Integer nextId;
    }

    public interface Progress {
        void update(long done, long total);
    }

    // ------------------------------------------------------------------ transport

    public Session session = new Session();

    public PicoApi() { }

    public PicoApi(Session s) { session = s; }

    static class Resp {
        JSONObject json;
        Map<String, List<String>> headers;
    }

    static String enc(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8");
        } catch (UnsupportedEncodingException e) {
            throw new RuntimeException(e);
        }
    }

    static String query(String[][] pairs) {
        StringBuilder sb = new StringBuilder();
        for (String[] p : pairs) {
            if (sb.length() > 0) sb.append('&');
            sb.append(enc(p[0])).append('=').append(enc(p[1]));
        }
        return sb.toString();
    }

    static byte[] readAll(InputStream in, int limit) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[16384];
        int n;
        while ((n = in.read(buf)) > 0) {
            out.write(buf, 0, n);
            if (out.size() > limit) throw new IOException("Response too large");
        }
        return out.toByteArray();
    }

    static List<String> header(Map<String, List<String>> h, String name) {
        List<String> out = new ArrayList<String>();
        for (Map.Entry<String, List<String>> e : h.entrySet()) {
            if (e.getKey() != null && e.getKey().equalsIgnoreCase(name)) out.addAll(e.getValue());
        }
        return out;
    }

    Resp request(String url, String body, String contentType, Map<String, String> extra,
                 boolean auth, int retries) throws PicoException {
        Exception last = null;
        for (int attempt = 0; attempt < retries; attempt++) {
            HttpURLConnection c = null;
            try {
                c = (HttpURLConnection) new URL(url).openConnection();
                c.setConnectTimeout(20000);
                c.setReadTimeout(30000);
                c.setUseCaches(false);
                c.setRequestProperty("User-Agent", UA);
                if (extra != null) {
                    for (Map.Entry<String, String> e : extra.entrySet()) {
                        c.setRequestProperty(e.getKey(), e.getValue());
                    }
                }
                if (auth) session.apply(c);
                if (body != null) {
                    byte[] data = body.getBytes("UTF-8");
                    c.setRequestMethod("POST");
                    c.setDoOutput(true);
                    c.setRequestProperty("Content-Type", contentType);
                    c.setFixedLengthStreamingMode(data.length);
                    OutputStream os = c.getOutputStream();
                    os.write(data);
                    os.close();
                }
                int code = c.getResponseCode();
                if (code == 401) throw new SessionExpired("Your PICO sign-in has expired. Sign in again.");
                if (code >= 500 || code == 429) {
                    last = new IOException("HTTP " + code);
                } else if (code >= 400) {
                    throw new PicoException("PICO returned HTTP " + code);
                } else {
                    String text = new String(readAll(c.getInputStream(), 8 << 20), "UTF-8").trim();
                    Matcher m = BIG_ID.matcher(text);
                    text = m.replaceAll("\"$1\":\"$2\"");
                    Resp r = new Resp();
                    try {
                        r.json = new JSONObject(text.length() == 0 ? "{}" : text);
                    } catch (JSONException e) {
                        throw new PicoException("PICO sent an unexpected reply (HTTP " + code + ")");
                    }
                    r.headers = c.getHeaderFields();
                    return r;
                }
            } catch (IOException e) {
                last = e;
            } finally {
                if (c != null) c.disconnect();
            }
            if (attempt + 1 < retries) sleep(1000L * (attempt + 1));
        }
        throw new PicoException("Could not reach PICO: " + (last == null ? "unknown" : last.getMessage()));
    }

    static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ignored) {
        }
    }

    String storeUrl(String path) {
        return STORE_HOST + path + "?" + query(new String[][]{
                {"manifest_version_code", "401200000"},
                {"device_name", "A9210"},
                {"uid", session.uid == null || session.uid.length() == 0 ? "0" : session.uid},
                {"app_id", "314431"},
                {"app_language", LANG},
                {"client_type", "1"},
                {"zone_name", "America/New_York"},
                {"timestamp", String.valueOf(System.currentTimeMillis() / 1000)},
        });
    }

    JSONObject storePost(String path, String body, boolean auth, int retries) throws PicoException {
        if (auth && !session.valid()) throw new PicoException("Sign in first.");
        Map<String, String> h = new LinkedHashMap<String, String>();
        h.put("Locale", LANG);
        h.put("sdk-version", "2");
        Resp r = request(storeUrl(path), body, "application/json", h, auth, retries);
        int code = r.json.optInt("code", -1);
        if (code != 0) {
            String msg = r.json.optString("msg", r.json.optString("message", "")).trim();
            String hint = auth && (code == 110001 || code == 10006 || code == 10008)
                    ? " - you may not own this app, or your sign-in expired (try signing in again)" : "";
            throw new PicoException("PICO store error " + code + ": "
                    + (msg.length() == 0 ? "no details" : msg) + hint);
        }
        JSONObject d = r.json.optJSONObject("data");
        return d == null ? new JSONObject() : d;
    }

    // ------------------------------------------------------------------ account

    static String encodeField(String v) {
        try {
            byte[] b = v.getBytes("UTF-8");
            StringBuilder sb = new StringBuilder();
            for (byte x : b) sb.append(String.format("%02x", (x ^ 5) & 0xff));
            return sb.toString();
        } catch (UnsupportedEncodingException e) {
            throw new RuntimeException(e);
        }
    }

    Resp passportPost(String path, String[][] fields) throws PicoException {
        String url = ACCOUNT_HOST + path + "?" + query(new String[][]{
                {"multi_login", "1"}, {"account_sdk_source", "app"},
                {"passport-sdk-version", "30490"}, {"aid", "308733"}, {"device_platform", "android"}});
        Resp r = request(url, query(fields), "application/x-www-form-urlencoded", null, false, 1);
        if (!"success".equals(r.json.optString("message"))) {
            JSONObject inner = r.json.optJSONObject("data");
            int code = inner != null ? inner.optInt("error_code", -1) : r.json.optInt("error_code", -1);
            String desc = inner != null ? inner.optString("description", "") : "";
            String msg;
            switch (code) {
                case 1009:
                    msg = "Email or password is incorrect. If you normally sign in with an email code, "
                            + "use the code option instead.";
                    break;
                case 7:
                    msg = "Too many requests. Wait a minute and try again.";
                    break;
                case 1104:
                case 1105:
                    msg = "PICO wants extra verification. Sign in once on PICO's website or headset, then try again.";
                    break;
                case 1202:
                case 1203:
                    msg = "The verification code is wrong or expired.";
                    break;
                default:
                    msg = ("PICO sign-in failed (code " + code + ") " + desc).trim();
            }
            throw new PicoException(msg);
        }
        return r;
    }

    Session sessionFromLogin(Resp r, String email) throws PicoException {
        Session s = new Session();
        s.email = email;
        for (String line : header(r.headers, "Set-Cookie")) {
            String pair = line.split(";", 2)[0];
            int eq = pair.indexOf('=');
            if (eq > 0) {
                String k = pair.substring(0, eq).trim(), v = pair.substring(eq + 1).trim();
                if (k.length() > 0 && v.length() > 0) s.cookies.put(k, v);
            }
        }
        List<String> tok = header(r.headers, "x-tt-token");
        if (!tok.isEmpty()) s.token = tok.get(0);
        JSONObject d = r.json.optJSONObject("data");
        String uid = d == null ? "" : d.optString("user_id_str", d.optString("user_id", ""));
        s.uid = uid.length() == 0 ? "0" : uid;
        if (!s.valid()) throw new PicoException("PICO accepted the sign-in but returned no session.");
        session = s;
        if ("0".equals(s.uid)) s.uid = accountUid();
        return s;
    }

    public Session loginPassword(String email, String password) throws PicoException {
        Resp r = passportPost("/passport/user/login/", new String[][]{
                {"mix_mode", "1"}, {"email", encodeField(email)}, {"password", encodeField(password)}});
        return sessionFromLogin(r, email);
    }

    public void sendCode(String email) throws PicoException {
        passportPost("/passport/email/send_code/", new String[][]{
                {"email", encodeField(email)}, {"type", encodeField("13")},
                {"email_logic_type", "0"}, {"mix_mode", "1"}});
    }

    public Session loginCode(String email, String code) throws PicoException {
        Resp r = passportPost("/passport/app/email/code_login/", new String[][]{
                {"email", encodeField(email)}, {"ect_type", "13"}, {"code", encodeField(code)},
                {"mix_mode", "1"}, {"email_logic_type", "0"}});
        return sessionFromLogin(r, email);
    }

    public String accountUid() throws PicoException {
        String url = ACCOUNT_HOST + "/passport/account/info/v2/?" + query(new String[][]{
                {"aid", "308733"}, {"device_platform", "android"}, {"passport-sdk-version", "30490"}});
        Resp r = request(url, null, null, null, true, 3);
        JSONObject d = r.json.optJSONObject("data");
        if (!"success".equals(r.json.optString("message")) || d == null) {
            throw new SessionExpired("Your PICO sign-in has expired. Sign in again.");
        }
        String uid = d.optString("user_id_str", d.optString("user_id", ""));
        if (uid.length() == 0) throw new SessionExpired("PICO did not return your account id. Sign in again.");
        return uid;
    }

    public void verifySession() throws PicoException {
        session.uid = accountUid();
    }

    // ------------------------------------------------------------------ store

    static String priceOf(JSONObject o) {
        return o.isNull("price") ? "" : o.optString("price", "");
    }

    public SearchPage search(String word, int nextId) throws PicoException {
        String body;
        try {
            String w = word.trim();
            if (w.length() > 100) w = w.substring(0, 100);
            body = new JSONObject().put("word", w)
                    .put("pageable", new JSONObject().put("next_id", nextId).put("size", 20)).toString();
        } catch (JSONException e) {
            throw new PicoException("Bad search");
        }
        JSONObject d = storePost("/api/app/v2/search/aggregation", body, false, 3);
        SearchPage page = new SearchPage();
        JSONArray groups = d.optJSONArray("search_list");
        if (groups == null) return page;
        java.util.HashSet<String> seen = new java.util.HashSet<String>();
        for (int i = 0; i < groups.length(); i++) {
            JSONObject g = groups.optJSONObject(i);
            if (g == null) continue;
            JSONArray items = g.optJSONArray("items");
            if (items != null) {
                for (int j = 0; j < items.length(); j++) {
                    JSONObject it = items.optJSONObject(j);
                    if (it == null) continue;
                    String iid = it.optString("item_id", ""), pkg = it.optString("package_name", "");
                    if (!ID.matcher(iid).matches() || !PKG.matcher(pkg).matches() || !seen.add(iid)) continue;
                    StoreApp a = new StoreApp(iid, pkg, it.optString("name", pkg));
                    if (a.name.length() == 0) a.name = pkg;
                    a.price = priceOf(it);
                    a.versionCode = it.optInt("version_code", 0);
                    page.apps.add(a);
                }
            }
            int cur = g.optInt("next_id", 0);
            if (g.optBoolean("has_more", false) && cur > 0 && page.nextId == null) page.nextId = cur;
        }
        return page;
    }

    /** Item details; when signed in, includes whether your account owns it. */
    public StoreApp item(String pkg) throws PicoException {
        if (!PKG.matcher(pkg).matches()) throw new PicoException("Not a valid package name: " + pkg);
        boolean auth = session.valid();
        String body;
        try {
            body = new JSONObject().put("package_name", pkg).toString();
        } catch (JSONException e) {
            throw new PicoException("Bad package");
        }
        JSONObject d = storePost("/api/app/v1/item/info", body, auth, 3);
        if (!pkg.equals(d.optString("package_name"))) throw new PicoException("PICO has no app with package " + pkg);
        StoreApp a = new StoreApp(d.optString("item_id"), pkg, d.optString("name", pkg));
        if (a.name.length() == 0) a.name = pkg;
        a.price = priceOf(d);
        a.currency = d.isNull("currency") ? "" : d.optString("currency", "");
        a.versionCode = d.optInt("version_code", 0);
        if (auth && d.has("entitlement_status")) a.owned = d.optInt("entitlement_status", 0) == 1;
        if (d.has("is_offer_exist")) a.offerExists = d.optBoolean("is_offer_exist");
        return a;
    }

    /** Same as pressing "Get" on a free app in the store: adds it to your PICO library. */
    public void addFreeApp(StoreApp a) throws PicoException {
        if (!a.isFree() || a.currency.length() == 0) throw new PicoException(a.name + " is not a free app.");
        if (!ID.matcher(a.itemId).matches()) throw new PicoException("Invalid app id");
        String body = "{\"item_id\":" + a.itemId + ",\"is_free_entitlment\":true,\"currency\":"
                + JSONObject.quote(a.currency) + ",\"amount\":" + JSONObject.quote(a.price)
                + ",\"support_cross_pay\":false}";
        JSONObject d = storePost("/api/app/v1/item/price", body, true, 1);
        if (!d.optBoolean("free", false)) throw new PicoException("PICO did not confirm the free order.");
    }

    public DownloadInfo downloadInfo(StoreApp a, boolean beta) throws PicoException {
        if (!ID.matcher(a.itemId).matches() || !PKG.matcher(a.pkg).matches()) throw new PicoException("Invalid app id");
        String body = "{\"item_id\":" + a.itemId + ",\"package_name\":" + JSONObject.quote(a.pkg)
                + ",\"incl_dev_preview\":" + (beta ? "true" : "false") + "}";
        JSONObject d = storePost("/api/app/v1/download/info", body, true, 3);
        JSONObject pkg = d.optJSONObject("package");
        if (pkg == null || !a.pkg.equals(pkg.optString("package_name", a.pkg))) {
            throw new PicoException("PICO returned no APK for this app.");
        }
        DownloadInfo info = new DownloadInfo();
        info.apk = fileFrom(pkg, a.pkg + ".apk");
        if (info.apk == null) throw new PicoException("PICO returned incomplete APK details.");
        info.version = pkg.optString("version", "");
        info.versionCode = pkg.optLong("version_code", 0);
        List<DownloadFile> all = new ArrayList<DownloadFile>();
        findFiles(d, all, 0);
        for (DownloadFile f : all) if (!f.url.equals(info.apk.url)) info.extras.add(f);
        return info;
    }

    static boolean urlOk(String u) {
        return u.startsWith("https://") || (allowHttpDownloads && u.startsWith("http://"));
    }

    static DownloadFile fileFrom(JSONObject o, String fallback) {
        String url = o.optString("path", o.optString("url", o.optString("download_url", "")));
        String md5 = o.optString("md5", "");
        if (!urlOk(url) || !MD5.matcher(md5).matches()) return null;
        String name = o.optString("file_name", "");
        if (name.length() == 0) name = fallback;
        if (name.length() == 0) {
            String p = url.split("\\?", 2)[0];
            name = p.substring(p.lastIndexOf('/') + 1);
        }
        if (name.length() == 0) name = "file.bin";
        return new DownloadFile(name, url, md5.toLowerCase(), o.optLong("size", 0));
    }

    /** Other downloadable files (e.g. OBB/expansion data) mentioned in download info. */
    static void findFiles(Object node, List<DownloadFile> out, int depth) {
        if (depth > 6) return;
        if (node instanceof JSONObject) {
            JSONObject o = (JSONObject) node;
            DownloadFile f = fileFrom(o, "");
            if (f != null) out.add(f);
            Iterator<String> it = o.keys();
            while (it.hasNext()) findFiles(o.opt(it.next()), out, depth + 1);
        } else if (node instanceof JSONArray) {
            JSONArray a = (JSONArray) node;
            for (int i = 0; i < a.length(); i++) findFiles(a.opt(i), out, depth + 1);
        }
    }

    public static String safeName(String s) {
        String t = s.replaceAll("[<>:\"/\\\\|?*\\x00-\\x1f]+", "_").trim();
        t = t.replaceAll("^[ ._]+|[ ._]+$", "");
        if (t.length() > 120) t = t.substring(0, 120);
        return t.length() == 0 ? "app" : t;
    }

    /** Download to dest (via dest.part), verify MD5, then rename. */
    public static File download(DownloadFile f, File dest, Progress progress, AtomicBoolean cancel,
                                int retries) throws PicoException {
        File part = new File(dest.getPath() + ".part");
        File dir = dest.getParentFile();
        if (dir != null) dir.mkdirs();
        String last = "unknown error";
        for (int attempt = 0; attempt < retries; attempt++) {
            HttpURLConnection c = null;
            OutputStream out = null;
            try {
                MessageDigest md = MessageDigest.getInstance("MD5");
                c = (HttpURLConnection) new URL(f.url).openConnection();
                c.setConnectTimeout(20000);
                c.setReadTimeout(60000);
                c.setRequestProperty("User-Agent", UA);
                int code = c.getResponseCode();
                if (code != 200) throw new IOException("HTTP " + code);
                long total = f.size > 0 ? f.size : c.getContentLength();
                InputStream in = c.getInputStream();
                out = new FileOutputStream(part);
                byte[] buf = new byte[256 * 1024];
                long done = 0;
                int n;
                long lastReport = 0;
                while ((n = in.read(buf)) > 0) {
                    if (cancel != null && cancel.get()) throw new Cancelled();
                    out.write(buf, 0, n);
                    md.update(buf, 0, n);
                    done += n;
                    long now = System.currentTimeMillis();
                    if (progress != null && now - lastReport > 150) {
                        lastReport = now;
                        progress.update(done, total);
                    }
                }
                out.close();
                out = null;
                if (progress != null) progress.update(done, total);
                StringBuilder hex = new StringBuilder();
                for (byte b : md.digest()) hex.append(String.format("%02x", b & 0xff));
                if (!hex.toString().equals(f.md5)) throw new PicoException("Checksum mismatch for " + dest.getName());
                if (dest.exists() && !dest.delete()) throw new IOException("Can't replace " + dest.getName());
                if (!part.renameTo(dest)) throw new IOException("Can't save " + dest.getName());
                return dest;
            } catch (Cancelled e) {
                closeQuietly(out);
                part.delete();
                throw e;
            } catch (PicoException e) {
                last = e.getMessage();
            } catch (IOException e) {
                last = e.getMessage();
            } catch (NoSuchAlgorithmException e) {
                throw new PicoException("MD5 not available");
            } finally {
                closeQuietly(out);
                if (c != null) c.disconnect();
            }
            part.delete();
            if (attempt + 1 < retries) sleep(1000L * (attempt + 1));
        }
        throw new PicoException("Download failed: " + last);
    }

    static void closeQuietly(OutputStream o) {
        if (o != null) {
            try {
                o.close();
            } catch (IOException ignored) {
            }
        }
    }
}
