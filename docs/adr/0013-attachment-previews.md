# 0013. Attachment previews with Pillow and pypdfium2, made by a worker

Status: Accepted, 2026-10-18

## Context

Files on a request were name chips; Dmitry asked for tiles with a preview (mockup: Attachment
previews). The viewer's strip also loaded every image whole, megabytes for a 64-pixel square.
A preview of an image or a PDF's first page has to be drawn by the server once, and Django
cannot read images or PDFs by itself.

## Decision

- **Pillow** makes images smaller (and turns phone photos upright by their EXIF data).
  **pypdfium2** draws a PDF's first page and counts its pages; it wraps PDFium, Chrome's PDF
  engine, ships as a wheel and needs no system packages. Both write a WebP no larger than
  320 × 240 (about 10–40 KB), stored next to the files in the private folder and sent only
  through `/files/<id>/preview/` with the same access check as the file.
- A worker command, `make_previews`, draws them (ADR 0004): the site never decodes a client's
  image or PDF, so a broken or hostile file can only slow or crash the worker. A file that
  cannot be read gets no preview; its tile shows the extension and the file still opens.
- A text file's first lines are kept at upload (no library needed); video and sound tiles are
  drawn without a picture. A frame from a video comes from the browser at upload, without FFmpeg
  on the server (next PR).

## Consequences

+ Tiles show what is inside a file; the viewer's strip loads kilobytes instead of megabytes.
+ About 0.1–0.5 s of worker time per file, once; about 30 KB of disk per preview.
− Two dependencies to keep up to date: both parse files from outside, so their security releases
  matter. Dependabot already watches `uv.lock`.
− A preview appears a few seconds after upload; until then the tile says "preview soon".
− If either library is abandoned, the other ways are ImageMagick or Poppler on the server.
