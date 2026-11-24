# Pico APK Grabber

Install consumer PICO Store apps on a **PICO 4 Enterprise**.

The Enterprise headset doesn't have the consumer PICO Store, so apps you own on your
PICO account (free apps you've claimed and games/apps you've bought) can't be installed
on it the normal way. This tool signs in to your PICO account, downloads the official
APKs of apps that account owns, and lets you sideload them to the Enterprise headset.

There are two versions:
- **Android app**, which runs on the Enterprise headset itself (or on a phone). It
  downloads and installs apps on the headset directly, with no PC needed. See
  `android/README.md`.
- **Desktop app** (Python, can be packaged as a Windows .exe), which downloads APKs on a
  PC so you can sideload them with SideQuest or `adb install`.

### On a PICO 4 Enterprise

1. Sideload `PicoApkGrabber.apk` (from **Releases**) once, using SideQuest or
   `adb install PicoApkGrabber.apk`.
2. Open it from the headset's app library (under unknown sources) and sign in with
   your consumer PICO account.
3. Search for an app you own and choose **Download and install on this device**.

Things to know:
- Only apps your account owns can be downloaded. Buy paid apps in the PICO Store
  (website, phone app or a consumer headset) first. Free apps can be added from inside
  the tool.
- Some consumer apps check your license or use PICO account features through PICO's
  platform services at launch. Those may not start on an Enterprise headset, because
  it doesn't run the consumer account services. Apps that don't make that check
  generally run fine.
- If a game comes with extra data (OBB) files, copy them to
  `Android/obb/<package name>/` on the headset.

It uses the same PICO Store calls as
[aeongdesu/pico-steamlink-apk](https://github.com/aeongdesu/pico-steamlink-apk)
(sign in, then `download/info`) plus the search and ownership calls documented by
[nkanf-dev/pico-store-lab](https://github.com/nkanf-dev/pico-store-lab).

## Desktop app

### Run it

- **From source:** install Python 3.10+ from python.org, then double-click
  `pico_apk_grabber.py` or run `py pico_apk_grabber.py`. It needs no packages beyond
  Python's standard library.
- **Build an .exe with auto-py-to-exe:** `pip install auto-py-to-exe`, run
  `auto-py-to-exe`, open *Settings → Import Config From JSON File*, choose
  `auto-py-to-exe-config.json`, re-select `pico_apk_grabber.py` under *Script Location*
  if it shows as missing, then click *Convert*.
- **Or build without the GUI:** double-click `build_windows.bat`. The .exe appears in
  `dist\PicoApkGrabber.exe`.

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
3. Select apps and click **Download selected**, or double-click a row. Each APK's MD5 is
   checked against PICO's value before the file is kept. If PICO also returns extra data
   files (OBB/expansion), they go into a `<name>_files` folder next to the APK.
4. Apps found as owned are saved to **My library**. Later, *Download all* gets newer
   versions and skips versions you already have.

*Prefer beta / preview build* asks for the developer-preview channel when an app has
one, as the Steam Link repo does.

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
real window, so it needs a display.
