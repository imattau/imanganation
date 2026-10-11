# Imanganation for Android

This is an Android companion app, starting with an offline canonical-script editor.
It opens and saves `.md` scripts through Android's document picker so a draft can be
moved to the desktop workflow without granting broad storage access.

## Why native Android first

The existing interface is a Python plug-in built around GIMP and GTK; there is no web
front end to package. Tauri can produce Android packages, and its current GitHub Action
has experimental mobile-build support, but adopting it here would still mean creating a
new web UI plus a Rust/Tauri integration layer. It would not reuse the current GIMP UI.

Jetpack Compose gives this first companion a native editor, Android document access and
platform text behavior with a smaller bridge surface. If the product later needs the
same rewritten UI on iOS and desktop, revisit Tauri or Compose Multiplatform then.

The Android app does not bundle GIMP, ComfyUI, model weights or image generation. Those
remain desktop responsibilities for now. A future online/desktop connection needs its
own explicit transport and authentication design; the current engine binds to localhost.

## Build

The GitHub Actions workflow builds a debug APK on changes to this directory, on pull
requests and when run manually. It attaches the APK as a 14-day workflow artifact; no
signing key or release credentials are needed for this development build.

```sh
cd android
gradle --no-daemon :app:assembleDebug
```

The output is `app/build/outputs/apk/debug/app-debug.apk`.

## First milestone

- Open a Markdown script with Android's file picker.
- Edit the canonical script text offline.
- Save a Markdown copy to a chosen location.
- Continue project creation, panel generation and page assembly in desktop GIMP.

The Android app is intentionally a script companion at this stage, not a mobile port of
the GIMP workspace.
