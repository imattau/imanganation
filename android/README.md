# Imanganation for Android

The target is a new, self-contained mobile-first Imanganation app inspired by the
desktop GIMP fork. It will provide project and script authoring, character and
reference management, image generation, and page assembly/export without requiring a
desktop or cloud service for its core workflow.

The current Compose app is a packaging and CI scaffold only. Its offline script editor
is not the product definition and does not yet provide generation or image editing.
See [the Android port architecture](../docs/android-port.md) for the feasibility
assessment, proposed boundaries, and milestones.

## Framework direction

The product app now uses React + TypeScript + Vite, Ionic React, PixiJS and Capacitor.
The Compose app remains a separate build proof. The existing GIMP app and Python engine
remain the behavior reference; they are not the new UI or Android runtime. See
[the mobile app architecture](../docs/android-port.md) for the boundaries and technical
gates.

The critical unknown is still local image generation on Android. The mobile inference
path must be proved before promising desktop feature parity.

## CI build

The GitHub Actions workflow builds a debug APK for mobile-app changes, pull requests,
and manual dispatch. Version tags named `imanganation-android-v*` build signed APK and
AAB artifacts using the four `ANDROID_*` signing secrets. No store publication is
configured.

```sh
cd mobile
npm ci
npm run build
npx cap sync android
cd android
./gradlew --no-daemon assembleDebug
```
