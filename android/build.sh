#!/usr/bin/env bash
# Builds PicoApkGrabber.apk without Gradle or Android Studio.
# Needs (Ubuntu/Debian, also works in WSL):
#   sudo apt install openjdk-17-jdk aapt apksigner zipalign dalvik-exchange android-sdk-platform-23
set -euo pipefail
cd "$(dirname "$0")"
ANDROID_JAR=${ANDROID_JAR:-/usr/lib/android-sdk/platforms/android-23/android.jar}
OUT=build
KEYSTORE=${KEYSTORE:-signing/release.jks}
KS_PASS=${KS_PASS:-picoapkgrabber}

rm -rf "$OUT" && mkdir -p "$OUT/classes" "$OUT/gen"

[ -f res/mipmap-mdpi/ic_launcher.png ] || python3 tools/make_icon.py

if [ ! -f "$KEYSTORE" ]; then
  mkdir -p "$(dirname "$KEYSTORE")"
  keytool -genkeypair -keystore "$KEYSTORE" -storepass "$KS_PASS" -keypass "$KS_PASS" \
    -alias grabber -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Pico APK Grabber" >/dev/null
fi

aapt package -f -m -J "$OUT/gen" -M AndroidManifest.xml -S res -I "$ANDROID_JAR" -F "$OUT/unsigned.apk"
find src "$OUT/gen" -name '*.java' > "$OUT/sources.txt"
javac -nowarn -Xlint:-options -source 8 -target 8 -encoding UTF-8 \
  -bootclasspath "$ANDROID_JAR" -d "$OUT/classes" @"$OUT/sources.txt"
dalvik-exchange --dex --min-sdk-version=23 --output="$OUT/classes.dex" "$OUT/classes"
(cd "$OUT" && aapt add unsigned.apk classes.dex >/dev/null)
zipalign -f 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
apksigner sign --ks "$KEYSTORE" --ks-pass "pass:$KS_PASS" --out PicoApkGrabber.apk "$OUT/aligned.apk"
apksigner verify PicoApkGrabber.apk
echo "Built $(pwd)/PicoApkGrabber.apk"
