# Pico APK Grabber for Android

> **Status: untested** on a real headset and with PICO's live servers. Please report
> problems with the text from the app's log.

Runs right on a PICO 4 Enterprise (or any Android phone or PICO headset). It downloads
the official APKs of consumer PICO Store apps your account owns and installs them on
the device it's running on, so you can get your consumer apps onto an Enterprise
headset without a PC.

## Install it

Download `PicoApkGrabber.apk` from this repo's **Releases** page (Android 6.0 or newer).

- **Phone:** copy the APK to the phone, open it, and allow installs from your browser or
  file manager when Android asks.
- **PICO headset:** sideload it like any APK (SideQuest, `adb install PicoApkGrabber.apk`,
  or open it from the headset's file manager). It then appears under **Unknown sources**
  in the headset's app library.

## Use it

1. Tap **Sign in** and use your PICO international account, with either your password or
   an email code.
2. Search by app name, or type an exact package name. Each app shows **Owned**,
   **Free - not in library**, or **Not owned**.
3. Tap an app to choose what to do:
   - **Download APK** saves the file after checking its MD5 against PICO's value.
   - **Download and install on this device** opens Android's install prompt. Use this on
     the headset. The first time, Android asks you to allow installs from Pico APK
     Grabber.
   - **Add to my PICO library (free)** does the same as pressing Get in the store.
   - **Open store page**: buy apps here if you don't own them yet.
4. Before the first download you pick where files go:
   - **A folder** (for example Download). Best on a phone, because you can copy or share
     the APK from there.
   - **App storage**, which is fine for installing on the same device. Files are in
     `Android/data/com.picoapkgrabber/files/APKs`.
5. **My library** keeps every app found as owned. **Download all** fetches newer versions
   and skips ones you already have.

## Notes

- The app always checks ownership with PICO before asking for a download link, and PICO
  refuses links for apps your account doesn't own.
- PICO has no public "list my library" call, so an app joins My library the first time you
  search for it while signed in.
- If PICO returns extra data files (OBB), they're saved in a `<name>_files` folder. Android
  doesn't let one app write another app's OBB folder, so copy them to
  `Android/obb/<package>/` yourself.
- Installing fails if a copy with a different signature or a newer version is already
  installed. Uninstall that copy first.
- Some paid games check your license at launch, so the headset may need to be signed in
  to the account that owns them.
- Your session is stored in the app's private storage. Your password is never saved.
  Sign out removes the session.

## Build it yourself

`./build.sh` builds and signs the APK without Gradle or Android Studio. It runs on
Ubuntu/Debian or WSL, with:

```
sudo apt install openjdk-17-jdk aapt apksigner zipalign dalvik-exchange android-sdk-platform-23
```

The first build creates its own signing key in `signing/`. An APK built with a new key
can't update the prebuilt one, so uninstall the prebuilt APK before installing your own
build. Keep `signing/` if you want later builds to update each other.

`python3 test/run_api_test.py` tests the store client against a fake PICO server on a
desktop JVM. It needs `libandroid-json-java` and the desktop `tests/` folder.
