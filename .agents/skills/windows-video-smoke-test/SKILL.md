---
name: windows-video-smoke-test
description: "Build or launch EveryAI on Windows and manually analyze videos from input/, pairing same-stem SRT files and covering videos without subtitles. Excludes CI-only build checks."
---

# Windows video smoke test

Use this skill when asked to build, open, or test this repository's desktop app on Windows with the repository's `input/` samples. The GitHub Actions Windows workflow only builds, installs, and checks startup; it cannot read the user's ignored local `input/` folder or validate video analysis.

## Prepare the app

1. Work from the repository root on a Windows 10/11 x64 machine. Check the current branch and worktree before changing files. Do not remove build output or app data unless the user asked for a clean build or reset.
2. Inspect `input/` in place. Do not search elsewhere for it when its location is already known. List video and `.srt` filenames and pair files by identical stem, case-insensitively. A video with no same-stem `.srt` is a no-subtitle case; do not create or infer a subtitle for it. Flag orphan subtitle files without a matching video.
3. If `dist\barrierfree\barrierfree.exe` exists, use it unless a fresh build was requested. Otherwise follow the Windows preparation and PyInstaller steps in `build/README.md`. The model-enabled build may download about 2.1 GB of models. Use `skip_models` only for a packaging/startup check, never for video analysis.
4. Alternatively, use `.github/workflows/build-windows.yml` to build an artifact, download and install it on the Windows test PC, then test the local samples there. A successful Actions run is not evidence that analysis works.

## Run the sample cases

1. Launch the app and verify that the main window opens without an immediate error.
2. For each video with a matching SRT, select the video and its same-stem subtitle in the registration screen. Confirm the displayed subtitle mode says a subtitle is present, then start analysis.
3. For representative videos without a matching SRT, select only the video. Follow the app's subtitle-region confirmation step if shown, then start analysis. Record this as the no-subtitle path; do not attach a different video's subtitle.
4. Wait for analysis to finish. For each case, check that results load, the video can be played where supported, and no error code is displayed. If analysis is cancelled, confirm the UI returns to a usable state.
5. Keep a concise result table with video filename, expected case (matching SRT / no SRT), observed subtitle mode, completion or error code, and playback outcome. Include every input set when requested; otherwise cover at least one example of each available case and state which files were tested.

## Diagnose failures

- Capture the exact error code and the relevant app log around the failure. The default Windows log is `%APPDATA%\barrierfree\barrierfree\logs\app.log`; confirm the actual path if the app configuration differs.
- Report the Windows version, whether the app came from `dist` or the installer, the tested filenames, and the step that failed. Redact personal information before sharing logs.
- Keep the sample media and subtitle files unchanged. Do not delete `%APPDATA%\barrierfree` or reset the database as routine troubleshooting; inspect the log and reproduce first.
- Distinguish build/startup success from completed media analysis. State exactly which cases ran and which remain unverified.
