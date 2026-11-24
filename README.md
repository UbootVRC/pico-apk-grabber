# Pico APK Grabber

Install consumer PICO Store apps on a **PICO 4 Enterprise**.

> **Status: untested.** So far this has only been tested against a fake PICO server and
> a fake ADB. It hasn't yet been tried with PICO's live servers, on a real headset, or
> with a real USB install. Please report problems with the text from the app's log.

The Enterprise headset doesn't have the consumer PICO Store, so apps you own on your
PICO account (free apps you've claimed and games/apps you've bought) can't be installed
on it the normal way. This tool signs in to your PICO account, downloads the official
APKs of apps that account owns, and lets you sideload them to the Enterprise headset.

There are two versions:
- **Android app**, which runs on the Enterprise headset itself (or on a phone). It
  downloads and installs apps on the headset directly, with no PC needed. See
  `android/README.md`.
- **Desktop app** (`PicoApkGrabber.exe` on Windows, or the Python script), which
  downloads APKs to your PC and can also install them on the headset over USB.

Both are on the **Releases** page.

### On a PICO 4 Enterprise, without a PC

1. Sideload `PicoApkGrabber.apk` (from **Releases**) once, using SideQuest or
   `adb install PicoApkGrabber.apk`.
2. Open it from the headset's app library (under unknown sources) and sign in with
   your consumer PICO account.
3. Search for an app you own and choose **Download and install on this device**.

### On a PICO 4 Enterprise, from a PC

1. Turn on USB debugging on the headset (once). The path is usually: **Settings →
   General → About**, tap **Software version** several times until developer options
   appear, then turn on **USB debugging** in the new **Developer** section.
2. Run `PicoApkGrabber.exe`, sign in, search for an app you own and click
   **Download + install on headset (USB)** with the headset plugged in.
3. Put the headset on and accept **Allow USB debugging** the first time.

Things to know:
- Only apps your account owns can be downloaded. Buy paid apps in the PICO Store
  (website, phone app or a consumer headset) first. Free apps can be added from inside
  the tool.
- Some consumer apps check your license or use PICO account features through PICO's
  platform services at launch. Those may not start on an Enterprise headset, because
  it doesn't run the consumer account services. Apps that don't make that check
  generally run fine.
- If a game comes with extra data (OBB) files, the PC install copies them into
  `Android/obb/<package name>/` on the headset for you. With the Android app you have to
  copy them there yourself.

It uses the same PICO Store calls as
[aeongdesu/pico-steamlink-apk](https://github.com/aeongdesu/pico-steamlink-apk)
(sign in, then `download/info`) plus the search and ownership calls documented by
[nkanf-dev/pico-store-lab](https://github.com/nkanf-dev/pico-store-lab).

## Desktop app

### Run it

- **Windows:** download `PicoApkGrabber.exe` from **Releases** and run it. It isn't
  code-signed, so Windows SmartScreen may say "Windows protected your PC". Click
  **More info → Run anyway**. Some antivirus programs also flag PyInstaller apps by
  mistake.
- **From source:** install Python 3.10+ from python.org, then double-click
  `pico_apk_grabber.py` or run `py pico_apk_grabber.py`. It needs no packages beyond
  Python's standard library.
- **Build an .exe with auto-py-to-exe:** `pip install auto-py-to-exe`, run
  `auto-py-to-exe`, open *Settings → Import Config From JSON File*, choose
  `auto-py-to-exe-config.json`, re-select `pico_apk_grabber.py` under *Script Location*
  if it shows as missing, then click *Convert*.
- **Or build without the GUI:** double-click `build_windows.bat`. The .exe appears in
  `dist\PicoApkGrabber.exe`.

The .exe on the Releases page is built by GitHub Actions
(`.github/workflows/build-windows.yml`) whenever a `v*` tag is pushed.

### Use it

1. **Sign in** with your PICO international account, using either your password or an
   email code. Use the code option if your account was created with code sign-in and has
   no password.
2. **Search** by app name, or type an exact package name (for example
   `com.valvesoftware.steamlinkvr`). Each result shows whether your account owns it:
   - **Owned** (green): ready to download.
   - **Free – not in library** (blue): click *Add free app to my library*, which does the
     same thing as pressing "Get" in the store, then download.
   - **Not owned** (grey): skipped. Buy it in the PICO Store first (*Open store page*).
3. Select apps, then either:
   - click **Download selected** (or double-click a row) to save the APKs to your PC, or
   - plug in the headset and click **Download + install on headset (USB)**. This saves the
     APKs to your PC the same way, then installs them on the headset and copies any extra
     data (OBB) files to the right folder. *Check headset* tests the connection.

   Each APK's MD5 is checked against PICO's value before the file is kept. Extra data
   files are saved in a `<name>_files` folder next to the APK.
4. Apps found as owned are saved to **My library**. Later, *Download all* gets newer
   versions and skips versions you already have.

*Prefer beta / preview build* asks for the developer-preview channel when an app has
one, as the Steam Link repo does.

### Installing over USB (ADB)

Installing on the headset uses ADB from Google's Android platform-tools. If ADB isn't
already on your PC (on the PATH), the app offers to download platform-tools from Google
the first time and keeps them in its settings folder. They aren't bundled because
Google's license limits redistribution.

If an install fails, the APK is still saved on your PC and the log says why. The most
common cause is a copy of the app signed differently, or a newer version, already on the
headset; uninstall it first.

## Notes

- PICO has no public "list my library" call, so an app joins *My library* the first time
  you search for it while signed in.
- The tool always checks ownership with PICO before asking for a download link, and
  PICO's server refuses links for apps your account doesn't own.
- Some paid apps check your license at launch through the PICO platform. A sideloaded
  APK may still need the headset signed in to the account that owns it.
- Only the international store is supported (not the mainland China store).
- If you tick *Keep me signed in*, your session cookie is saved in
  `%APPDATA%\PicoApkGrabber\session.json` (on Linux/macOS,
  `~/.config/pico-apk-grabber/`). Your password is never saved. *Sign out* deletes the
  session file.
- This depends on PICO's unofficial app API, which can change without notice. If
  sign-in or downloads start failing, compare against the two repos above.

## Tests

`tests/` contains a fake PICO server plus API and GUI tests:
`python tests/test_grabber.py` and `python tests/test_gui.py`. The GUI test drives the
real window, so it needs a display. `python tests/test_adb.py` tests USB installs
against a fake ADB (`tests/fake_adb.py`, Linux/macOS only).
