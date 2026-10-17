# Changelog

## 0.1.3 — 2026-10-17

Version 0.1.2 → 0.1.3.

- feat: Update settings management to support legacy paths and enhance data environment handling

Drafted from commit messages by the publish workflow.

## 0.1.2 — 2026-10-17

Version 0.1.1 → 0.1.2.

- feat: Enhance folder selection and settings management for data directory

Drafted from commit messages by the publish workflow.

## 0.1.1 — 2026-10-11

Version 0.1.0 → 0.1.1.

- Implement code changes to enhance functionality and improve performance
- feat: Add developer guide and update README for improved onboarding

Drafted from commit messages by the publish workflow.

## 0.1.0 — 2026-10-10

First release with a Windows installer: 0.1.0.

- NOFACE now ships as a Windows installer. Download `NOFACE-win-x64-Setup.exe`
  from this release and run it; no Python, uv, or setup script is needed.
- The installed app keeps its workbooks, journal database, and logs in
  `Documents\NOFACE`. On first run it copies the example workbooks there so every
  tab opens immediately. Set the `NOFACE_HOME` environment variable to use another
  folder.
- The installed app checks this repository for new releases when it starts,
  downloads them in the background, and installs them after NOFACE closes.
- Running from source with `setup.ps1` and `NoFace.lnk` works as before.
