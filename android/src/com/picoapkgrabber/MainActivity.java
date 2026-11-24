package com.picoapkgrabber;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.PendingIntent;
import android.content.ActivityNotFoundException;
import android.content.ContentResolver;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInstaller;
import android.database.Cursor;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.DocumentsContract;
import android.text.InputType;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.widget.AdapterView;
import android.widget.BaseAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.CompoundButton;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import com.picoapkgrabber.PicoApi.Cancelled;
import com.picoapkgrabber.PicoApi.DownloadFile;
import com.picoapkgrabber.PicoApi.DownloadInfo;
import com.picoapkgrabber.PicoApi.PicoException;
import com.picoapkgrabber.PicoApi.SearchPage;
import com.picoapkgrabber.PicoApi.Session;
import com.picoapkgrabber.PicoApi.SessionExpired;
import com.picoapkgrabber.PicoApi.StoreApp;

import org.json.JSONArray;
import org.json.JSONException;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;

public class MainActivity extends Activity {
    static final String ACTION_INSTALL_STATUS = "com.picoapkgrabber.INSTALL_STATUS";
    static final String MIME_APK = "application/vnd.android.package-archive";
    static final int REQ_TREE = 1;
    static final int GREEN = 0xff5cc46a, BLUE = 0xff6aaef0, GREY = 0xff9a9a9a;

    final PicoApi api = new PicoApi();
    SharedPreferences prefs;
    Handler main;
    final LinkedHashMap<String, StoreApp> results = new LinkedHashMap<String, StoreApp>();
    final LinkedHashMap<String, StoreApp> library = new LinkedHashMap<String, StoreApp>();
    final List<StoreApp> shown = new ArrayList<StoreApp>();
    final AtomicBoolean cancel = new AtomicBoolean(false);
    volatile int checkGen = 0;
    boolean libraryTab = false, busy = false;
    Integer nextId = null;
    String lastQuery = "";
    Runnable afterFolder;

    TextView accountText, folderText, progressText, logText, emptyText;
    Button signBtn, tabSearch, tabLibrary, moreBtn, cancelBtn;
    EditText searchEdit;
    CheckBox ownedOnly, betaBox;
    LinearLayout searchBar, libraryBar;
    ListView list;
    ProgressBar progress;
    ScrollView logScroll;
    AppAdapter adapter;

    // ================================================================ lifecycle

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("main", MODE_PRIVATE);
        main = new Handler(Looper.getMainLooper());
        loadLibrary();
        buildUi();
        String saved = prefs.getString("session", null);
        final Session s = Session.fromJson(saved);
        if (s.valid()) {
            api.session = s;
            accountText.setText("Checking saved sign-in...");
            bg(new Job<Session>() {
                Session run() throws Exception {
                    new PicoApi(s).verifySession();
                    return s;
                }

                void ok(Session r) { setSession(r, true); }

                void fail(Exception e) {
                    if (e instanceof SessionExpired) {
                        log("Saved sign-in expired. Please sign in again.");
                        signOut(true);
                    } else {
                        log("Couldn't verify saved sign-in (" + e.getMessage() + "); keeping it.");
                        refreshAccount();
                    }
                }
            });
        } else {
            refreshAccount();
        }
        handleInstallIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handleInstallIntent(intent);
    }

    // ================================================================ UI

    int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }

    Button button(String text, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setOnClickListener(l);
        return b;
    }

    LinearLayout row() {
        LinearLayout r = new LinearLayout(this);
        r.setOrientation(LinearLayout.HORIZONTAL);
        r.setGravity(Gravity.CENTER_VERTICAL);
        return r;
    }

    static LinearLayout.LayoutParams weight(float w) {
        return new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, w);
    }

    void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(12), dp(8), dp(12), dp(8));

        LinearLayout top = row();
        accountText = new TextView(this);
        accountText.setTypeface(Typeface.DEFAULT_BOLD);
        top.addView(accountText, weight(1));
        signBtn = button("Sign in", new View.OnClickListener() {
            public void onClick(View v) {
                if (api.session.valid()) confirmSignOut();
                else showSignIn();
            }
        });
        top.addView(signBtn);
        root.addView(top);

        LinearLayout tabs = row();
        tabSearch = button("Search store", new View.OnClickListener() {
            public void onClick(View v) { setTab(false); }
        });
        tabLibrary = button("My library", new View.OnClickListener() {
            public void onClick(View v) { setTab(true); }
        });
        tabs.addView(tabSearch, weight(1));
        tabs.addView(tabLibrary, weight(1));
        root.addView(tabs);

        searchBar = new LinearLayout(this);
        searchBar.setOrientation(LinearLayout.VERTICAL);
        LinearLayout sr = row();
        searchEdit = new EditText(this);
        searchEdit.setHint("App name or package name");
        searchEdit.setSingleLine(true);
        searchEdit.setImeOptions(EditorInfo.IME_ACTION_SEARCH);
        searchEdit.setOnEditorActionListener(new TextView.OnEditorActionListener() {
            public boolean onEditorAction(TextView v, int id, KeyEvent e) {
                doSearch();
                return true;
            }
        });
        sr.addView(searchEdit, weight(1));
        sr.addView(button("Search", new View.OnClickListener() {
            public void onClick(View v) { doSearch(); }
        }));
        searchBar.addView(sr);
        LinearLayout sr2 = row();
        ownedOnly = new CheckBox(this);
        ownedOnly.setText("Owned only");
        ownedOnly.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            public void onCheckedChanged(CompoundButton b, boolean c) { refreshList(); }
        });
        sr2.addView(ownedOnly, weight(1));
        moreBtn = button("More results", new View.OnClickListener() {
            public void onClick(View v) { searchMore(); }
        });
        moreBtn.setEnabled(false);
        sr2.addView(moreBtn);
        searchBar.addView(sr2);
        root.addView(searchBar);

        libraryBar = new LinearLayout(this);
        libraryBar.setOrientation(LinearLayout.VERTICAL);
        TextView libHint = new TextView(this);
        libHint.setText("Apps found as owned are saved here so you can grab updates later. PICO has no "
                + "'list my library' call, so search for an app once to add it.");
        libHint.setTextColor(GREY);
        libraryBar.addView(libHint);
        libraryBar.addView(button("Download all (skips versions you already have)", new View.OnClickListener() {
            public void onClick(View v) {
                startDownloads(new ArrayList<StoreApp>(library.values()), false, true);
            }
        }));
        root.addView(libraryBar);

        emptyText = new TextView(this);
        emptyText.setTextColor(GREY);
        emptyText.setPadding(0, dp(12), 0, dp(12));
        root.addView(emptyText);

        list = new ListView(this);
        adapter = new AppAdapter();
        list.setAdapter(adapter);
        list.setOnItemClickListener(new AdapterView.OnItemClickListener() {
            public void onItemClick(AdapterView<?> p, View v, int pos, long id) { showActions(shown.get(pos)); }
        });
        root.addView(list, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));

        LinearLayout fr = row();
        folderText = new TextView(this);
        fr.addView(folderText, weight(1));
        fr.addView(button("Change folder", new View.OnClickListener() {
            public void onClick(View v) { askFolder(); }
        }));
        root.addView(fr);

        LinearLayout br = row();
        betaBox = new CheckBox(this);
        betaBox.setText("Prefer beta / preview builds");
        betaBox.setChecked(prefs.getBoolean("beta", false));
        br.addView(betaBox, weight(1));
        cancelBtn = button("Cancel", new View.OnClickListener() {
            public void onClick(View v) { cancel.set(true); }
        });
        cancelBtn.setEnabled(false);
        br.addView(cancelBtn);
        root.addView(br);

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(1000);
        root.addView(progress);
        progressText = new TextView(this);
        root.addView(progressText);

        logScroll = new ScrollView(this);
        logText = new TextView(this);
        logText.setTypeface(Typeface.MONOSPACE);
        logText.setTextSize(11);
        logText.setTextIsSelectable(true);
        logScroll.addView(logText);
        root.addView(logScroll, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(110)));

        setContentView(root);
        updateFolderText();
        setTab(false);
    }

    void setTab(boolean lib) {
        libraryTab = lib;
        tabSearch.setEnabled(lib);
        tabLibrary.setEnabled(!lib);
        searchBar.setVisibility(lib ? View.GONE : View.VISIBLE);
        libraryBar.setVisibility(lib ? View.VISIBLE : View.GONE);
        refreshList();
    }

    void refreshList() {
        shown.clear();
        if (libraryTab) {
            shown.addAll(library.values());
            Collections.sort(shown, new Comparator<StoreApp>() {
                public int compare(StoreApp a, StoreApp b) { return a.name.compareToIgnoreCase(b.name); }
            });
            emptyText.setText(shown.isEmpty() ? "No saved apps yet. Search the store while signed in." : "");
        } else {
            for (StoreApp a : results.values()) {
                if (!ownedOnly.isChecked() || Boolean.TRUE.equals(a.owned)) shown.add(a);
            }
            emptyText.setText(shown.isEmpty()
                    ? (results.isEmpty() ? "Search for an app by name, or type an exact package name." : "No owned apps in these results.")
                    : "");
        }
        emptyText.setVisibility(shown.isEmpty() ? View.VISIBLE : View.GONE);
        adapter.notifyDataSetChanged();
    }

    String statusFor(StoreApp a) {
        if (libraryTab && (a.owned == null || !api.session.valid())) return "Saved";
        if (!api.session.valid()) return "Sign in to check";
        return a.statusText();
    }

    class AppAdapter extends BaseAdapter {
        public int getCount() { return shown.size(); }

        public Object getItem(int i) { return shown.get(i); }

        public long getItemId(int i) { return i; }

        public View getView(int i, View convert, ViewGroup parent) {
            LinearLayout v;
            if (convert instanceof LinearLayout) {
                v = (LinearLayout) convert;
            } else {
                v = new LinearLayout(MainActivity.this);
                v.setOrientation(LinearLayout.VERTICAL);
                v.setPadding(dp(4), dp(8), dp(4), dp(8));
                TextView name = new TextView(MainActivity.this);
                name.setTextSize(16);
                name.setTypeface(Typeface.DEFAULT_BOLD);
                TextView pkg = new TextView(MainActivity.this);
                pkg.setTextSize(12);
                pkg.setTextColor(GREY);
                TextView status = new TextView(MainActivity.this);
                status.setTextSize(13);
                v.addView(name);
                v.addView(pkg);
                v.addView(status);
            }
            StoreApp a = shown.get(i);
            String st = statusFor(a);
            ((TextView) v.getChildAt(0)).setText(a.name);
            ((TextView) v.getChildAt(1)).setText(a.pkg);
            TextView status = (TextView) v.getChildAt(2);
            status.setText(a.priceText() + "  ·  " + st);
            status.setTextColor("Owned".equals(st) ? GREEN : (a.isFree() ? BLUE : GREY));
            return v;
        }
    }

    // ================================================================ background helpers

    abstract class Job<T> {
        abstract T run() throws Exception;

        void ok(T result) { }

        void fail(Exception e) { handleError(e); }
    }

    <T> void bg(final Job<T> job) {
        new Thread(new Runnable() {
            public void run() {
                try {
                    final T r = job.run();
                    main.post(new Runnable() {
                        public void run() { job.ok(r); }
                    });
                } catch (final Exception e) {
                    main.post(new Runnable() {
                        public void run() { job.fail(e); }
                    });
                }
            }
        }).start();
    }

    void log(String text) {
        String line = new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date()) + "  " + text;
        CharSequence old = logText.getText();
        String all = old.length() == 0 ? line : old + "\n" + line;
        if (all.length() > 20000) all = all.substring(all.length() - 20000);
        logText.setText(all);
        logScroll.post(new Runnable() {
            public void run() { logScroll.fullScroll(View.FOCUS_DOWN); }
        });
    }

    void logBg(final String text) {
        main.post(new Runnable() {
            public void run() { log(text); }
        });
    }

    void toast(String s) { Toast.makeText(this, s, Toast.LENGTH_LONG).show(); }

    void handleError(Exception e) {
        log("Error: " + e.getMessage());
        if (e instanceof SessionExpired) {
            signOut(true);
            new AlertDialog.Builder(this).setMessage(e.getMessage()).setPositiveButton("Sign in",
                    new DialogInterface.OnClickListener() {
                        public void onClick(DialogInterface d, int w) { showSignIn(); }
                    }).setNegativeButton("Later", null).show();
        } else if (!(e instanceof PicoException)) {
            toast(e.toString());
        }
    }

    void hideKeyboard() {
        InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
        if (imm != null) imm.hideSoftInputFromWindow(searchEdit.getWindowToken(), 0);
    }

    // ================================================================ account

    void showSignIn() {
        LinearLayout v = new LinearLayout(this);
        v.setOrientation(LinearLayout.VERTICAL);
        v.setPadding(dp(20), dp(8), dp(20), dp(8));
        TextView intro = new TextView(this);
        intro.setText("Use your PICO international account.");
        v.addView(intro);
        final EditText email = new EditText(this);
        email.setHint("Email");
        email.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS);
        email.setText(prefs.getString("email", ""));
        v.addView(email);
        final EditText pw = new EditText(this);
        pw.setHint("Password");
        pw.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        v.addView(pw);
        final Button pwBtn = new Button(this);
        pwBtn.setText("Sign in with password");
        pwBtn.setAllCaps(false);
        v.addView(pwBtn);
        TextView or = new TextView(this);
        or.setText("— or use an email code —");
        or.setGravity(Gravity.CENTER);
        or.setPadding(0, dp(10), 0, dp(4));
        v.addView(or);
        final Button sendBtn = new Button(this);
        sendBtn.setText("Send code to my email");
        sendBtn.setAllCaps(false);
        v.addView(sendBtn);
        final EditText code = new EditText(this);
        code.setHint("Code from the email");
        code.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        v.addView(code);
        final Button codeBtn = new Button(this);
        codeBtn.setText("Sign in with code");
        codeBtn.setAllCaps(false);
        v.addView(codeBtn);
        final CheckBox remember = new CheckBox(this);
        remember.setText("Keep me signed in on this device");
        remember.setChecked(prefs.getBoolean("remember", true));
        v.addView(remember);
        final TextView msg = new TextView(this);
        msg.setPadding(0, dp(6), 0, 0);
        v.addView(msg);

        ScrollView sv = new ScrollView(this);
        sv.addView(v);
        final AlertDialog d = new AlertDialog.Builder(this).setTitle("Sign in to PICO").setView(sv)
                .setNegativeButton("Close", null).create();

        final Runnable[] setBusy = new Runnable[2];
        setBusy[0] = new Runnable() {
            public void run() { pwBtn.setEnabled(false); sendBtn.setEnabled(false); codeBtn.setEnabled(false); }
        };
        setBusy[1] = new Runnable() {
            public void run() { pwBtn.setEnabled(true); sendBtn.setEnabled(true); codeBtn.setEnabled(true); }
        };

        class LoginJob extends Job<Session> {
            final boolean usePassword;
            final String e, secret;

            LoginJob(boolean usePassword, String e, String secret) {
                this.usePassword = usePassword;
                this.e = e;
                this.secret = secret;
            }

            Session run() throws Exception {
                PicoApi p = new PicoApi();
                return usePassword ? p.loginPassword(e, secret) : p.loginCode(e, secret);
            }

            void ok(Session s) {
                prefs.edit().putString("email", s.email).putBoolean("remember", remember.isChecked()).apply();
                d.dismiss();
                setSession(s, remember.isChecked());
            }

            void fail(Exception ex) {
                setBusy[1].run();
                msg.setTextColor(0xffe57373);
                msg.setText(ex.getMessage());
            }
        }

        final Object[] dummy = new Object[0];
        pwBtn.setOnClickListener(new View.OnClickListener() {
            public void onClick(View x) {
                String e = email.getText().toString().trim(), p = pw.getText().toString();
                if (!PicoApi.EMAIL.matcher(e).matches() || p.length() == 0) {
                    msg.setText("Enter your email and password.");
                    return;
                }
                setBusy[0].run();
                msg.setTextColor(GREY);
                msg.setText("Signing in...");
                bg(new LoginJob(true, e, p));
            }
        });
        sendBtn.setOnClickListener(new View.OnClickListener() {
            public void onClick(View x) {
                final String e = email.getText().toString().trim();
                if (!PicoApi.EMAIL.matcher(e).matches()) {
                    msg.setText("Enter a valid email address.");
                    return;
                }
                setBusy[0].run();
                msg.setTextColor(GREY);
                msg.setText("Sending code...");
                bg(new Job<Object>() {
                    Object run() throws Exception {
                        new PicoApi().sendCode(e);
                        return dummy;
                    }

                    void ok(Object r) {
                        setBusy[1].run();
                        msg.setTextColor(GREEN);
                        msg.setText("Code sent to " + e + ". Check your inbox.");
                        code.requestFocus();
                    }

                    void fail(Exception ex) {
                        setBusy[1].run();
                        msg.setTextColor(0xffe57373);
                        msg.setText(ex.getMessage());
                    }
                });
            }
        });
        codeBtn.setOnClickListener(new View.OnClickListener() {
            public void onClick(View x) {
                String e = email.getText().toString().trim(), c = code.getText().toString().trim();
                if (!PicoApi.EMAIL.matcher(e).matches() || c.length() == 0) {
                    msg.setText("Enter your email and the code.");
                    return;
                }
                setBusy[0].run();
                msg.setTextColor(GREY);
                msg.setText("Signing in...");
                bg(new LoginJob(false, e, c));
            }
        });
        d.show();
    }

    void setSession(Session s, boolean remember) {
        api.session = s;
        prefs.edit().putString("session", remember ? s.toJson() : null).apply();
        log("Signed in as " + (s.email.length() > 0 ? s.email : "PICO user") + ".");
        refreshAccount();
        checkOwnership(new ArrayList<StoreApp>(results.values()), false);
        checkOwnership(new ArrayList<StoreApp>(library.values()), true);
    }

    void confirmSignOut() {
        new AlertDialog.Builder(this).setMessage("Sign out of PICO on this device?")
                .setPositiveButton("Sign out", new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface d, int w) { signOut(false); }
                }).setNegativeButton("Cancel", null).show();
    }

    void signOut(boolean quiet) {
        api.session = new Session();
        prefs.edit().remove("session").apply();
        checkGen++;
        for (StoreApp a : results.values()) a.owned = null;
        if (!quiet) log("Signed out.");
        refreshAccount();
    }

    void refreshAccount() {
        Session s = api.session;
        if (s.valid()) {
            accountText.setText("Signed in: " + (s.email.length() > 0 ? s.email : "PICO account"));
            signBtn.setText("Sign out");
        } else {
            accountText.setText("Not signed in");
            signBtn.setText("Sign in");
        }
        refreshList();
    }

    // ================================================================ search & ownership

    void doSearch() {
        final String word = searchEdit.getText().toString().trim();
        if (word.length() == 0) return;
        hideKeyboard();
        lastQuery = word;
        results.clear();
        nextId = null;
        moreBtn.setEnabled(false);
        refreshList();
        log("Searching for '" + word + "'...");
        runSearch(word, 1, true);
    }

    void searchMore() {
        if (nextId != null) runSearch(lastQuery, nextId, false);
    }

    void runSearch(final String word, final int page, final boolean tryPackage) {
        bg(new Job<SearchPage>() {
            SearchPage run() throws Exception {
                StoreApp direct = null;
                if (tryPackage && PicoApi.PKG.matcher(word).matches()) {
                    try {
                        direct = api.item(word);
                    } catch (PicoException ignored) {
                    }
                }
                SearchPage p = api.search(word, page);
                if (direct != null) p.apps.add(0, direct);
                return p;
            }

            void ok(SearchPage p) {
                if (!word.equals(lastQuery)) return;
                nextId = p.nextId;
                moreBtn.setEnabled(nextId != null);
                List<StoreApp> added = new ArrayList<StoreApp>();
                for (StoreApp a : p.apps) {
                    if (!results.containsKey(a.itemId)) {
                        results.put(a.itemId, a);
                        added.add(a);
                    }
                }
                log(results.size() + " app(s) found." + (api.session.valid() ? "" : " Sign in to see which you own."));
                refreshList();
                checkOwnership(added, false);
            }
        });
    }

    void checkOwnership(final List<StoreApp> apps, final boolean fromLibrary) {
        if (apps.isEmpty() || !api.session.valid()) return;
        if (!fromLibrary) checkGen++;
        final int gen = checkGen;
        new Thread(new Runnable() {
            public void run() {
                int owned = 0;
                for (final StoreApp a : apps) {
                    if (gen != checkGen) return;
                    try {
                        final StoreApp d = api.item(a.pkg);
                        if (Boolean.TRUE.equals(d.owned)) owned++;
                        main.post(new Runnable() {
                            public void run() {
                                if (gen == checkGen) applyItem(a, d, fromLibrary);
                            }
                        });
                    } catch (final SessionExpired e) {
                        main.post(new Runnable() {
                            public void run() { handleError(e); }
                        });
                        return;
                    } catch (PicoException e) {
                        logBg(a.name + ": " + e.getMessage());
                    }
                    PicoApi.sleep(150);
                }
                if (!fromLibrary) logBg("Ownership checked: " + owned + " owned.");
            }
        }).start();
    }

    void applyItem(StoreApp a, StoreApp d, boolean fromLibrary) {
        a.copyFrom(d);
        if (Boolean.TRUE.equals(a.owned)) {
            StoreApp l = library.get(a.itemId);
            if (l == null) {
                try {
                    l = StoreApp.fromJson(a.toJson());
                } catch (JSONException e) {
                    return;
                }
                library.put(a.itemId, l);
                log("Added to My library: " + a.name);
            }
            if (l != a) l.copyFrom(a);
            saveLibrary();
        }
        refreshList();
    }

    void loadLibrary() {
        library.clear();
        try {
            JSONArray arr = new JSONArray(prefs.getString("library", "[]"));
            for (int i = 0; i < arr.length(); i++) {
                StoreApp a = StoreApp.fromJson(arr.getJSONObject(i));
                if (PicoApi.ID.matcher(a.itemId).matches() && PicoApi.PKG.matcher(a.pkg).matches()) {
                    library.put(a.itemId, a);
                }
            }
        } catch (JSONException ignored) {
        }
    }

    void saveLibrary() {
        JSONArray arr = new JSONArray();
        try {
            for (StoreApp a : library.values()) arr.put(a.toJson());
        } catch (JSONException ignored) {
        }
        prefs.edit().putString("library", arr.toString()).apply();
    }

    // ================================================================ actions

    void showActions(final StoreApp a) {
        final List<String> labels = new ArrayList<String>();
        final List<Runnable> acts = new ArrayList<Runnable>();
        final List<StoreApp> one = Collections.singletonList(a);
        boolean maybeOwned = Boolean.TRUE.equals(a.owned) || (libraryTab && a.owned == null);
        if (maybeOwned) {
            labels.add("Download APK");
            acts.add(new Runnable() {
                public void run() { startDownloads(one, false, false); }
            });
            labels.add("Download and install on this device");
            acts.add(new Runnable() {
                public void run() { startDownloads(one, true, false); }
            });
        }
        if (a.isFree() && Boolean.FALSE.equals(a.owned)) {
            labels.add("Add to my PICO library (free)");
            acts.add(new Runnable() {
                public void run() { addFree(a); }
            });
        }
        if (!api.session.valid()) {
            labels.add("Sign in to check ownership");
            acts.add(new Runnable() {
                public void run() { showSignIn(); }
            });
        }
        labels.add("Open store page");
        acts.add(new Runnable() {
            public void run() {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(a.storeUrl())));
                } catch (ActivityNotFoundException e) {
                    toast("No browser found. Store page: " + a.storeUrl());
                }
            }
        });
        if (libraryTab) {
            labels.add("Remove from this list");
            acts.add(new Runnable() {
                public void run() {
                    library.remove(a.itemId);
                    saveLibrary();
                    refreshList();
                }
            });
        }
        String sub = a.priceText() + " · " + statusFor(a);
        new AlertDialog.Builder(this).setTitle(a.name + "\n" + sub)
                .setItems(labels.toArray(new String[0]), new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface d, int which) { acts.get(which).run(); }
                }).show();
    }

    void addFree(final StoreApp a) {
        new AlertDialog.Builder(this).setMessage("Add the free app \"" + a.name + "\" to your PICO library? "
                + "This is the same as pressing Get in the PICO Store.")
                .setPositiveButton("Add", new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface d, int w) {
                        bg(new Job<StoreApp>() {
                            StoreApp run() throws Exception {
                                api.addFreeApp(a);
                                return a;
                            }

                            void ok(StoreApp r) {
                                log("Added free app to your PICO library: " + a.name);
                                checkOwnership(Collections.singletonList(a), false);
                            }
                        });
                    }
                }).setNegativeButton("Cancel", null).show();
    }

    // ================================================================ storage

    Uri treeUri() {
        String s = prefs.getString("tree", null);
        return s == null ? null : Uri.parse(s);
    }

    boolean folderDecided() { return treeUri() != null || prefs.getBoolean("app_storage", false); }

    File appStorageDir() {
        File base = getExternalFilesDir(null);
        if (base == null) base = getFilesDir();
        return new File(base, "APKs");
    }

    void updateFolderText() {
        Uri t = treeUri();
        if (t != null) {
            String seg = t.getLastPathSegment();
            if (seg != null && seg.contains(":")) seg = seg.substring(seg.indexOf(':') + 1);
            folderText.setText("Save to: " + (seg == null || seg.length() == 0 ? "chosen folder" : seg));
        } else if (prefs.getBoolean("app_storage", false)) {
            folderText.setText("Save to: app storage");
        } else {
            folderText.setText("Save to: not chosen yet");
        }
    }

    void askFolder() {
        new AlertDialog.Builder(this).setTitle("Where should APKs be saved?")
                .setItems(new String[]{
                        "Choose a folder (e.g. Download) — easy to copy or share",
                        "App storage — fine for installing on this device"
                }, new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface d, int which) {
                        if (which == 0) {
                            pickFolder();
                        } else {
                            prefs.edit().putBoolean("app_storage", true).remove("tree").apply();
                            updateFolderText();
                            log("Saving to " + appStorageDir());
                            runAfterFolder();
                        }
                    }
                }).setOnCancelListener(new DialogInterface.OnCancelListener() {
                    public void onCancel(DialogInterface d) { afterFolder = null; }
                }).show();
    }

    void pickFolder() {
        try {
            Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
            i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                    | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
            startActivityForResult(i, REQ_TREE);
        } catch (ActivityNotFoundException e) {
            toast("This device has no folder picker, so APKs will go to app storage.");
            prefs.edit().putBoolean("app_storage", true).remove("tree").apply();
            updateFolderText();
            runAfterFolder();
        }
    }

    void runAfterFolder() {
        Runnable r = afterFolder;
        afterFolder = null;
        if (r != null) r.run();
    }

    @Override
    protected void onActivityResult(int req, int result, Intent data) {
        super.onActivityResult(req, result, data);
        if (req != REQ_TREE) return;
        if (result == RESULT_OK && data != null && data.getData() != null) {
            Uri uri = data.getData();
            try {
                getContentResolver().takePersistableUriPermission(uri,
                        Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
            } catch (SecurityException e) {
                log("Couldn't keep access to that folder: " + e.getMessage());
            }
            prefs.edit().putString("tree", uri.toString()).putBoolean("app_storage", false).apply();
            updateFolderText();
            runAfterFolder();
        } else {
            afterFolder = null;
        }
    }

    Uri treeRoot(Uri tree) {
        return DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree));
    }

    Uri findChild(Uri tree, Uri parent, String name) {
        Uri children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, DocumentsContract.getDocumentId(parent));
        Cursor c = getContentResolver().query(children, new String[]{
                DocumentsContract.Document.COLUMN_DOCUMENT_ID, DocumentsContract.Document.COLUMN_DISPLAY_NAME},
                null, null, null);
        if (c == null) return null;
        try {
            while (c.moveToNext()) {
                if (name.equals(c.getString(1))) return DocumentsContract.buildDocumentUriUsingTree(tree, c.getString(0));
            }
        } finally {
            c.close();
        }
        return null;
    }

    boolean storageHas(String name) {
        Uri tree = treeUri();
        if (tree == null) return new File(appStorageDir(), name).exists();
        try {
            return findChild(tree, treeRoot(tree), name) != null;
        } catch (Exception e) {
            return false;
        }
    }

    static void copy(InputStream in, OutputStream out) throws IOException {
        byte[] buf = new byte[256 * 1024];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
    }

    /** Save a verified file to the chosen folder (or app storage). Returns a description of where. */
    String store(File src, String name, String mime, String subdir) throws IOException {
        Uri tree = treeUri();
        if (tree == null) {
            File dir = subdir == null ? appStorageDir() : new File(appStorageDir(), subdir);
            dir.mkdirs();
            File dest = new File(dir, name);
            InputStream in = new FileInputStream(src);
            OutputStream out = new FileOutputStream(dest);
            try {
                copy(in, out);
            } finally {
                in.close();
                out.close();
            }
            return dest.getPath();
        }
        try {
            ContentResolver cr = getContentResolver();
            Uri parent = treeRoot(tree);
            if (subdir != null) {
                Uri dir = findChild(tree, parent, subdir);
                if (dir == null) {
                    dir = DocumentsContract.createDocument(cr, parent, DocumentsContract.Document.MIME_TYPE_DIR, subdir);
                }
                if (dir == null) throw new IOException("Couldn't create folder " + subdir);
                parent = dir;
            }
            Uri existing = findChild(tree, parent, name);
            if (existing != null) DocumentsContract.deleteDocument(cr, existing);
            Uri doc = DocumentsContract.createDocument(cr, parent, mime, name);
            if (doc == null) throw new IOException("Couldn't create " + name);
            OutputStream out = cr.openOutputStream(doc);
            if (out == null) throw new IOException("Couldn't write " + name);
            InputStream in = new FileInputStream(src);
            try {
                copy(in, out);
            } finally {
                in.close();
                out.close();
            }
            return (subdir == null ? "" : subdir + "/") + name;
        } catch (IOException e) {
            throw e;
        } catch (Exception e) {
            throw new IOException("Couldn't save to the chosen folder (" + e.getMessage()
                    + "). Use 'Change folder' to pick it again.");
        }
    }

    // ================================================================ downloads

    void setBusy(boolean b) {
        busy = b;
        cancelBtn.setEnabled(b);
        if (b) {
            cancel.set(false);
            getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        } else {
            getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            progress.setProgress(0);
            progressText.setText("");
        }
    }

    PicoApi.Progress progressFor(final String name) {
        return new PicoApi.Progress() {
            public void update(final long done, final long total) {
                main.post(new Runnable() {
                    public void run() {
                        if (total > 0) {
                            progress.setProgress((int) (done * 1000 / total));
                            progressText.setText(String.format(Locale.US, "%s: %.1f / %.1f MB", name,
                                    done / 1048576.0, total / 1048576.0));
                        } else {
                            progressText.setText(String.format(Locale.US, "%s: %.1f MB", name, done / 1048576.0));
                        }
                    }
                });
            }
        };
    }

    void startDownloads(final List<StoreApp> apps, final boolean install, final boolean skipExisting) {
        if (busy) {
            toast("A download is already running.");
            return;
        }
        if (apps.isEmpty()) {
            toast("Nothing to download.");
            return;
        }
        if (!api.session.valid()) {
            toast("Sign in to your PICO account first.");
            showSignIn();
            return;
        }
        if (!folderDecided()) {
            afterFolder = new Runnable() {
                public void run() { startDownloads(apps, install, skipExisting); }
            };
            askFolder();
            return;
        }
        final boolean beta = betaBox.isChecked();
        prefs.edit().putBoolean("beta", beta).apply();
        setBusy(true);
        final File work = new File(getCacheDir(), "dl");
        work.mkdirs();
        new Thread(new Runnable() {
            public void run() {
                int ok = 0, failed = 0;
                for (StoreApp a : apps) {
                    if (cancel.get()) break;
                    File tmp = null;
                    try {
                        // Always confirm ownership with PICO before asking for a link.
                        StoreApp cur = api.item(a.pkg);
                        if (!Boolean.TRUE.equals(cur.owned)) {
                            throw new PicoException(cur.isFree()
                                    ? "skipped - free but not in your library yet (tap it and choose 'Add to my PICO library')"
                                    : "skipped - your account doesn't own it");
                        }
                        DownloadInfo info = api.downloadInfo(cur, beta);
                        String stem = PicoApi.safeName(cur.name + "_v" + info.version + "_" + info.versionCode);
                        String apkName = stem + ".apk";
                        if (skipExisting && storageHas(apkName)) {
                            logBg(cur.name + ": already have " + apkName);
                            ok++;
                            continue;
                        }
                        logBg(cur.name + ": downloading v" + info.version + " (" + info.versionCode + ")"
                                + (info.apk.size > 0 ? String.format(Locale.US, ", %.1f MB", info.apk.size / 1048576.0) : ""));
                        tmp = new File(work, apkName);
                        PicoApi.download(info.apk, tmp, progressFor(cur.name), cancel, 3);
                        String where = store(tmp, apkName, MIME_APK, null);
                        logBg(cur.name + ": saved " + where + " (MD5 verified)");
                        for (DownloadFile x : info.extras) {
                            String xn = PicoApi.safeName(x.name);
                            File xt = new File(work, xn);
                            logBg(cur.name + ": extra data file " + xn);
                            try {
                                PicoApi.download(x, xt, progressFor(xn), cancel, 3);
                                logBg(cur.name + ": saved " + store(xt, xn, "application/octet-stream", stem + "_files"));
                            } finally {
                                xt.delete();
                            }
                        }
                        if (!info.extras.isEmpty()) {
                            logBg("Note: " + cur.name + " has extra data files. To play, copy them into "
                                    + "Android/obb/" + cur.pkg + "/ on the headset.");
                        }
                        if (install) {
                            logBg(cur.name + ": starting install...");
                            installApk(tmp);
                        }
                        ok++;
                    } catch (Cancelled e) {
                        logBg("Cancelled.");
                        break;
                    } catch (final SessionExpired e) {
                        main.post(new Runnable() {
                            public void run() { handleError(e); }
                        });
                        break;
                    } catch (PicoException e) {
                        failed++;
                        logBg(a.name + ": " + e.getMessage());
                    } catch (Exception e) {
                        failed++;
                        logBg(a.name + ": " + e);
                    } finally {
                        if (tmp != null) tmp.delete();
                    }
                }
                final int okF = ok, failedF = failed;
                main.post(new Runnable() {
                    public void run() {
                        setBusy(false);
                        log("Done: " + okF + " downloaded"
                                + (failedF > 0 ? ", " + failedF + " not downloaded (see above)" : ""));
                    }
                });
            }
        }).start();
    }

    // ================================================================ install

    void installApk(File apk) throws IOException {
        PackageInstaller pi = getPackageManager().getPackageInstaller();
        PackageInstaller.SessionParams params =
                new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        params.setSize(apk.length());
        int id = pi.createSession(params);
        PackageInstaller.Session s = pi.openSession(id);
        boolean committed = false;
        try {
            OutputStream out = s.openWrite("base.apk", 0, apk.length());
            InputStream in = new FileInputStream(apk);
            try {
                copy(in, out);
                s.fsync(out);
            } finally {
                in.close();
                out.close();
            }
            Intent i = new Intent(this, MainActivity.class).setAction(ACTION_INSTALL_STATUS);
            int flags = PendingIntent.FLAG_UPDATE_CURRENT;
            if (Build.VERSION.SDK_INT >= 31) flags |= 0x02000000; // FLAG_MUTABLE: installer adds extras
            PendingIntent p = PendingIntent.getActivity(this, id, i, flags);
            s.commit(p.getIntentSender());
            committed = true;
        } finally {
            if (!committed) s.abandon();
            s.close();
        }
    }

    void handleInstallIntent(Intent i) {
        if (i == null || !ACTION_INSTALL_STATUS.equals(i.getAction())) return;
        int st = i.getIntExtra(PackageInstaller.EXTRA_STATUS, -999);
        String pkg = i.getStringExtra(PackageInstaller.EXTRA_PACKAGE_NAME);
        if (st == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            Intent confirm = i.getParcelableExtra(Intent.EXTRA_INTENT);
            if (confirm != null) {
                try {
                    startActivity(confirm);
                } catch (Exception e) {
                    log("Couldn't open the install prompt: " + e.getMessage());
                }
            }
        } else if (st == PackageInstaller.STATUS_SUCCESS) {
            log("Installed " + (pkg == null ? "app" : pkg) + ".");
            toast("Installed");
        } else if (st != -999) {
            String m = i.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
            log("Install failed: " + (m == null ? "status " + st : m)
                    + (st == PackageInstaller.STATUS_FAILURE_CONFLICT
                    ? " (a different or newer copy is already installed - uninstall it first)" : ""));
        }
        setIntent(new Intent(this, MainActivity.class));
    }
}
