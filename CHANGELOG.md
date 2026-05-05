# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [0.2.1] — 2026-05-05

### Fixed

- `HIDResource.send_shortcut` was sending the multi-value query parameter as
  `key=...` (singular); kvmd validates `keys` (plural) and rejected the
  request with HTTP 400 (#19).
- `StreamerState` model rewritten to match the actual `/api/streamer`
  response: top-level `features` / `limits` / `params` / `snapshot` /
  `streamer` (the latter is `null` when no stream clients are connected).
  The previous top-level `enabled` and `source` fields never existed in the
  API and `get_state()` always raised `pydantic.ValidationError` (#18).
- `StreamerResource.ocr()` was calling the wrong endpoint
  (`/api/streamer/ocr`, which returns capability metadata) and parsing
  capability JSON as a string. It now sends
  `GET /api/streamer/snapshot?ocr=1` and returns plain text. Added a
  `langs` parameter (comma-separated by kvmd) and a 30 s default per-call
  timeout — Tesseract on the Pi takes 10–20 s for full-screen OCR,
  exceeding the client default (#21).

### Added

- `StreamerResource.get_ocr_info()` and `OCRInfo` / `OCRLangs` models for
  `GET /api/streamer/ocr` capability metadata (replaces the
  always-broken `OCRResult`).
- `allow_offline` parameter on `StreamerResource.snapshot()` and
  `StreamerResource.ocr()`. When the video source is offline (host asleep,
  HDMI unplugged) but the streamer process is still running, kvmd returns
  HTTP 503 by default; with `allow_offline=True` it returns a
  "NO LIVE VIDEO" placeholder JPEG (or its OCR'd text). The flag has no
  effect when the streamer process is fully stopped — that is a kvmd
  lifecycle limitation (#22).
- Optional `timeout` argument on `PiKVM.request()` and
  `BaseResource._get_raw()` for per-call timeout overrides.

### Changed

- New typed submodels for streamer state: `Streamer`, `StreamerEncoder`,
  `StreamerH264`, `StreamerSinkInfo`, `StreamerSinks`, `StreamerStream`,
  `StreamerLimits`, `StreamerLimitRange`, `StreamerFeatures`,
  `StreamerParams`, `StreamerSnapshot`. `StreamerSource` now exposes
  `captured_fps` and `desired_fps` in addition to `online` and
  `resolution`.

### Removed

- `OCRResult` model — replaced by `OCRInfo` (capability metadata) and a
  plain `str` return from `ocr()`. The old model never matched any real
  response.

## [0.1.1] — 2026-02-16

### Added

- Initial release
- Resources: auth, atx, hid, msd, gpio, streamer, switch, redfish, prometheus
- WebSocket client for realtime events and HID input
- Pydantic v2 response models
- Exception hierarchy
