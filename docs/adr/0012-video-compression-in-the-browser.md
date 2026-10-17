# 0012. Video is compressed in the browser with mediabunny

Status: Accepted, 2026-10-17

## Context

Clients record bugs on their phones. Two minutes of iPhone video is 50–150 MB, in HEVC that
Chrome on Windows often cannot play. Attachments are capped at 20 MB for video (PRD: access and
security, attachments). Re-encoding on the server would need FFmpeg on the VPS and a background
job per upload, and the client would still upload the 150 MB first.

Browsers can encode video themselves through the WebCodecs API. Using it directly means
reading MP4 and WebM containers and writing new ones by hand: several thousand lines.

## Decision

Before upload, the browser makes a video ready with mediabunny (MPL-2.0, no dependencies): at
most 2 minutes, the shorter side at most 720 px, H.264 in MP4 (or VP9/VP8 in WebM where H.264
is not available), at most 20 MB. A clip that already fits and plays everywhere is sent as it
is. A video over 2 minutes is refused with a hint to share a link.

The library is one file, `static/vendor/mediabunny/mediabunny.min.mjs` (version 1.61.5, 690 KB,
180 KB gzipped), committed with its licence. There is no npm and no build step (ADR 0006). The
form loads it only when someone attaches a video.

To update: download the package (`npm pack mediabunny`), copy `dist/bundles/mediabunny.min.mjs`
and `LICENSE` over the vendored files, change the version above, and check by hand that a
1080p clip still compresses.

## Consequences

+ No video processing on the server; clients upload a few MB instead of 100.
+ The PM can play every video in any browser.
− Compressing takes time on the client's device: about as long as the clip on a laptop.
− A browser without WebCodecs (old Safari, some in-app browsers) cannot compress: it may still
  send a video under 20 MB as it is; a bigger one is refused with a hint to share a link.
− The server still checks only size and type, not the duration: a client who bypasses the form
  can upload a long video up to 20 MB. That is acceptable for a closed portal.
− If mediabunny is abandoned, the vendored file keeps working; replacing it means another
  WebCodecs library or server-side FFmpeg.
