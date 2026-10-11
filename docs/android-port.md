# Imanganation mobile app architecture

## Product direction

Build a new mobile-first application inspired by the Imanganation GIMP fork. GIMP is
the reference for workflows and behavior, not a library or runtime dependency. The
Android app should have its own UI, project handling, generation controls, and page
assembly/export.

The intended self-contained workflow is:

- create and edit manga projects and canonical scripts;
- manage characters, identity references, locations, and props;
- generate individual panels with character consistency and staging controls;
- review, reroll, and save results into projects;
- assemble panels into pages and export usable image files.

The core workflow should run on the device after models are installed. A desktop or
cloud renderer could be an optional backend later, but cannot be required for the app
to count as the mobile edition.

## Why this direction

There are real GIMP-on-Android routes. Termux packages GIMP 3.2.6 for Android ARM64
and runs its X11 interface through Termux:X11; UserLAnd provides a Linux userland that
can run desktop GIMP. They prove that GIMP can be made to run, but neither gives us a
purpose-built touch interface. Our fork is a 3.3.1 development build, so using the
Termux path would also mean maintaining an Android build and its package/runtime.

The upstream GIMP team says that a mobile GIMP would need a redesigned interface and
has no promised Android release. For Imanganation, a fresh app lets us build the
workflow around phones and tablets instead of carrying the full desktop UI.

The Flatpak's CUDA ComfyUI renderer is a separate constraint. The current Linux stack
cannot be bundled directly in an Android app. Android inference runtimes exist, but
they do not automatically run our ComfyUI graphs or custom nodes. SDXL generation,
IP-Adapter identity references, regional conditioning, inpainting/refiner passes, and
memory/thermal behavior need their own proof on target devices.

## Mobile renderer candidates

There are real on-device Android diffusion options. The key question is whether their
model/control support matches the manga workflow, not whether they can produce a
single text-to-image sample.

| Candidate | What it offers | Fit and open questions |
|---|---|---|
| [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) | C/C++ engine; Android paths; Vulkan/OpenCL; SDXL; IP-Adapter Plus for SD 1.5/SDXL; LoRA; ControlNet for SD 1.5; VAE tiling and ESRGAN. | Best first engine spike: closest match to our existing SDXL + IP-Adapter stack and can be integrated through JNI. Check reference preprocessing, model conversion, Android ABI/backends, and whether multi-character regional conditioning can be reproduced. Its API is actively changing. |
| [Local Dream](https://github.com/xororz/local-dream) | Working Android app backed by stable-diffusion.cpp, MNN, and Qualcomm QNN; SD 1.5 and SDXL; Snapdragon NPU acceleration. The project says SDXL NPU support targets Snapdragon 8 Gen 3 and newer. | Best working-app reference and a candidate to benchmark on Snapdragon flagships. Its model packaging and runtime integration are worth studying. Verify exact model/control coverage and license before reusing code. |
| [MNN Diffusion](https://github.com/alibaba/MNN/tree/master/transformers/diffusion) | Native Android build with OpenCL; documented SD 1.5 and SD 3.5 pipelines; model conversion to MNN format. | A credible runtime alternative, especially if its mobile model conversion/performance wins. Current docs show text-to-image samples; no confirmed parity for IP-Adapter Plus, regional conditioning, or our inpaint/refiner graph. |
| [Qualcomm AI Hub / QNN](https://aihub.qualcomm.com/models) | Device-validated NPU model components; Qualcomm examples cover SD 1.5 and SD 3.5, with Android Snapdragon targets. | Potential acceleration path for supported Snapdragon devices, but it is vendor-specific and requires model export/conversion, device-specific validation, and an app-side diffusion pipeline. It does not by itself supply our graph and controls. |
| [React Native ExecuTorch text-to-image](https://docs.swmansion.com/react-native-executorch/docs/extensions/text-to-image) | React Native integration and a ready-to-run, distilled SDXS-512 one-step 512×512 pipeline. | Fastest way to prove the UI-to-native-runtime bridge, but the shipped model is a small text-to-image path; it does not establish SDXL, IP-Adapter identity, or regional composition. |
| [LiteRT Bonsai Image 4B](https://github.com/google-ai-edge/litert-samples/tree/main/models/bonsai/bonsai_image_4b) | A newer three-graph, 4-step diffusion pipeline with quantized 4B weights and LiteRT host/runtime code. | Worth watching as a compact-model direction. Its sample currently documents 512×512 output and an iPhone CPU benchmark, not a validated Android generation profile or our reference-guided features. |

[Stable Diffusion AI](https://github.com/ShiftHackZ/Stable-Diffusion-KMP) is also a
useful app-level comparison: its Android build exposes multiple local engines,
including ONNX, MediaPipe, stable-diffusion.cpp SDXL, and experimental Bonsai. Its
provider matrix currently marks Android local image generation as text-to-image only.
That is useful evidence that wrapping several runtimes is practical, while also
showing the gap between mobile text-to-image and the image-to-image/inpaint workflow we
need.

**Recommendation:** run a narrow head-to-head spike with stable-diffusion.cpp and
Local Dream first, using a representative NoobAI/Illustrious SDXL model and our real
character references. Include MNN as the alternate runtime if the model conversion and
device results are promising. Keep the React/Capacitor UI independent of the runtime so
we can add a device-specific NPU backend without changing the project workflow.

## Recommended stack

The Android app is bootstrapped as **React + TypeScript + Vite**, using **Ionic React**
for mobile controls and **Capacitor** for the Android shell. PixiJS provides the page
preview surface; Lucide React supplies icons; project records and large assets use
IndexedDB inside the app. This is an Android product target, not a PWA release. Use
Kotlin plug-ins where web APIs do not cover persistent file access, model downloads, or
long-running inference jobs.

Capacitor is the first choice over Tauri here because the product's new UI and shared
web app are the main goal; Rust is not needed to host that UI. Tauri remains a valid
alternative if we later decide to put substantial shared application logic in Rust.
Neither wrapper solves local diffusion by itself.

The current Compose app remains a CI proof. The `mobile/` Capacitor project contains the
product UI and Android shell.

## Current implementation status

The app currently supports local project creation, import/export of version 1 project
ZIPs, canonical script editing with bounded revision snapshots, panel and reference
management, basic panel placement, layered ORA page creation, lettering/effect overlays,
direct panel/effect repositioning, imported artwork takes with explicit active-take
selection, vertical-comic slicing, CBZ, PDF and page-image ZIP exports. IndexedDB stores project
records and file assets; imported unknown files are carried through project export.
Existing XCF page assets are preserved but cannot be previewed or edited. The Pages
workspace now uses a canvas-first GIMP-inspired workbench with a tool shelf, page
strip, tabbed Layers / Filters / Manga docks, editable ORA raster-layer visibility and
opacity, persistent grayscale and contrast effects, and zoom controls.

The Android APK builds and packages the pinned stable-diffusion.cpp C/C++ runtime for
ARM64 and queries its linked revision and basic device memory/ABI profile through a
Capacitor/JNI bridge. The native SDXL model-load path and initial 512×512 CPU
text-to-image route now produce a PNG take without changing the active take. These are
integration proofs only: model loading and rendering have not been checked on target
hardware, and model-file presence does not establish compatibility. Reroll/refine,
inpaint, image references, accelerator backends, and target-device quality, heat, and
latency checks remain open. Native sampling progress and cancellation are connected
through the Android bridge. The
authoring UI is a first implementation scaffold and does not yet provide full desktop
workflow parity, freeform painting, layer reordering/groups/masks, the full GEGL filter
catalog, or validated ORA round-trip checks in GIMP. The CI workflow builds debug APKs and signed release APK/AAB
artifacts from version tags; signing secrets are documented in the Android README.

## GIMP-inspired editor workbench plan

Use GIMP as the interaction reference: keep the canvas central, place frequent tools
within reach, and treat docks as persistent work surfaces. Adapt the arrangement to
touch instead of shrinking desktop controls to phone size.

1. **Canvas workbench:** provide a menu/command bar, tool shelf, canvas stage, zoom
   controls, page strip, and status/read-order information. Keep other manga workflows
   in Story, Panels, People, Create, and Export views.
2. **Dock system:** keep Layers, Filters, and Manga content in tabbed docks on wider
   screens. On phones, move the tool shelf above the canvas and turn the dock into a
   compact lower panel. Preserve the selected page and dock while users work.
3. **Project-backed layers:** read the ORA stack rather than its flattened preview.
   Persist nested groups, layer order, visibility, opacity, masks, and effect settings
   in project metadata while retaining unknown ORA assets.
4. **Non-destructive effects:** start with a small set of manga-usable effects, preview
   them on canvas, and apply the same settings when producing page, book, and vertical
   exports. Keep source raster layers intact.
5. **Expand editing parity:** add group opacity, direct mask painting, history/undo,
   non-destructive selection operations, stroke refinement, and a broader filter catalog
   as project storage and ORA interchange behavior are defined.

The current implementation covers the workbench, responsive dock layout, nested layer
groups, layer reordering, visibility and opacity, selection-derived masks, a persisted
grayscale/contrast effect stack, and export-time composition. It also has rectangular,
elliptical, and contiguous-color selection; layer translation, scale, and rotation; and
project-backed brush strokes with an eraser for those strokes. Group hierarchy, masks,
and strokes remain app-side project metadata while source ORA pixels are preserved.
Group opacity, painting directly on mask channels, undo/history, precise stroke splitting
for erasing, and GIMP-native GEGL effect interchange remain follow-up work.

## System shape

```text
React + TypeScript mobile-first UI
    ├── Shared project and script model (validated against project schema)
    ├── Character/reference and page/panel workspaces
    ├── Generation-job UI and result review
    └── Capacitor bridge
           ├── Kotlin project storage / Android document access
           ├── Kotlin download and foreground job lifecycle
           └── Android renderer adapter (native CPU text-to-image; generation controls expanding)

Desktop GIMP plug-in ── existing Python engine ── ComfyUI desktop renderer
                         (behavior reference and desktop product)
```

Reuse the repository's project-container schema, script format, generation inputs,
prompt rules, character/reference conventions, and export semantics where they are
UI-independent. Do not try to run the GIMP plug-in in the web app. Extract or port
domain rules behind stable data contracts instead of copying GTK interaction patterns.

The existing engine binds its API and ComfyUI listener to `127.0.0.1`. It is useful as
the desktop behavior reference, but it is not an Android backend as-is. Maintain two
renderer adapters behind the same generation request/result contract: ComfyUI for
desktop and an Android-compatible backend once proven. Preserve enough project
metadata that assets can move between both clients.

## Technical gates and milestones

1. **Product inventory:** map each Flatpak/GIMP action, project asset, and persisted
   setting to the new mobile workflow. Mark first-release scope and tablet-only
   workflows explicitly.
2. **Project contract:** validate that the existing schema and sample projects can be
   read and written from TypeScript without data loss. Define stable request/result
   contracts for generation, errors, progress, and cancellation.
3. **Mobile renderer spike:** compare stable-diffusion.cpp and Local Dream first, then
   MNN or a distilled LiteRT/ExecuTorch path if useful. Select supported device tiers;
   benchmark model download size, peak memory, latency, heat, power, and output quality.
   Demonstrate a panel and reference-guided generation path, including identity and
   staging behavior, before choosing the production runtime.
4. **App shell and project library:** implement import/export, storage estimates,
   app-managed working copies, autosave/recovery, and project browsing.
5. **Authoring workflow:** build script/page navigation, character and reference
   library, panel settings, generation queue, and result review for phones and tablets.
6. **Generation and model setup:** add signed/checksummed model downloads, model
   licensing/attribution, visible long-running jobs, cancellation, and process recovery.
7. **Page assembly/export:** compose panels according to layouts and export PNG/JPEG
   plus project interchange data.
8. **Release readiness:** device coverage, accessibility, battery/thermal limits,
   privacy, signed artifacts, and distribution decision.

The renderer spike is an early decision gate: do not imply that a web UI makes the
current desktop generation stack mobile-compatible. If full-quality local generation
does not fit target hardware, revisit supported device tiers and model scope before
building the rest of the application around a false parity assumption.

## Research references

- [Capacitor documentation](https://capacitorjs.com/docs) describes a web-first runtime
  for Android, iOS, and web with native plug-ins.
- [Capacitor Android documentation](https://next.capacitorjs.com/docs/next/android)
  describes its Java/Kotlin bridge and Android project.
- [Tauri 2](https://v2.tauri.app/start/) can also host web frontends and native backend
  logic; it remains an option if Rust becomes part of the desired shared core.
- [Termux GIMP package recipe](https://github.com/termux/termux-packages/blob/master/x11-packages/gimp/build.sh)
  builds GIMP for Termux's Android package ecosystem.
- [Termux:X11](https://github.com/termux/termux-x11) documents the Android X server and
  third-party app loader path.
- [UserLAnd](https://userland.tech/) documents Linux userlands and desktop GIMP on
  Android.
- [GIMP FAQ](https://www.gimp.org/docs/userfaq.html#will-you-release-gimp-for-android-or-ios-devices)
  explains that official mobile GIMP would need a redesigned interface and has no
  promised release.
- [ComfyUI upstream](https://github.com/comfy-org/ComfyUI) documents desktop Python
  installation and desktop GPU variants.
- [ExecuTorch on Android](https://docs.pytorch.org/executorch/stable/using-executorch-android.html)
  documents Android bindings and CPU/Vulkan/vendor backends.
- [LiteRT on Android](https://developer.android.com/ai/custom) documents the Android
  runtime and hardware delegates.
- [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) documents
  Android deployment, SDXL, IP-Adapter Plus, LoRA, and Vulkan/OpenCL backends.
- [Local Dream](https://github.com/xororz/local-dream) is an Android app using
  stable-diffusion.cpp, MNN, and Qualcomm QNN, with documented SDXL support on newer
  Snapdragon devices.
- [MNN Diffusion](https://github.com/alibaba/MNN/tree/master/transformers/diffusion)
  documents Android diffusion builds and model pipelines.
- [Qualcomm AI Hub](https://aihub.qualcomm.com/models) lists validated model variants
  and target Snapdragon devices.
- [React Native ExecuTorch](https://docs.swmansion.com/react-native-executorch/docs/extensions/text-to-image)
  documents its SDXS-512 Android text-to-image example.
- [LiteRT Bonsai Image 4B](https://github.com/google-ai-edge/litert-samples/tree/main/models/bonsai/bonsai_image_4b)
  documents a quantized 4-step diffusion pipeline.
- [Stable Diffusion AI](https://github.com/ShiftHackZ/Stable-Diffusion-KMP) provides an
  app-level reference for multiple local Android renderers and capability reporting.
