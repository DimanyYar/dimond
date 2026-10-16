// The attachment viewer: images and PDFs open over the card instead of in a new tab, with
// zoom for images, arrows and thumbnails to go through every file. Without this script the
// same links still open each file in a new tab.

const viewer = document.getElementById("media-viewer");
const thumbs = [...viewer.querySelectorAll("[data-viewer-thumb]")];
const stage = viewer.querySelector("[data-viewer-stage]");
const image = viewer.querySelector("[data-viewer-image]");
const pdf = viewer.querySelector("[data-viewer-pdf]");
const zoomGroup = viewer.querySelector("[data-viewer-zoom]");
const zoomLabel = viewer.querySelector("[data-viewer-zoom-label]");
const ZOOM_MIN = 50;
const ZOOM_MAX = 200;
let index = 0;
// null is "Fit" (shown as 100%): the whole image in the window. A number is a percentage of
// that size.
let zoom = null;
let fitWidth = 0;

viewer.classList.toggle("is-single", thumbs.length === 1);

function setZoom(value) {
  // Measured while the image still fits, so 100% is the size "Fit" showed. An image that has
  // not loaded yet has no width to scale, so zooming waits until it has one.
  if (zoom === null && value !== null) {
    if (!image.offsetWidth) return;
    fitWidth = image.offsetWidth;
  }
  zoom = value === null ? null : Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, value));
  zoomLabel.textContent = `${zoom ?? 100}%`;
  stage.classList.toggle("is-zoomed", zoom !== null);
  image.style.width = zoom === null ? "" : `${Math.round((fitWidth * zoom) / 100)}px`;
}

function show(next) {
  index = (next + thumbs.length) % thumbs.length;
  const file = thumbs[index].dataset;
  viewer.querySelector("[data-viewer-name]").textContent = file.name;
  viewer.querySelector("[data-viewer-meta]").textContent = file.meta;
  viewer.querySelector("[data-viewer-download]").href = file.download;
  viewer.querySelector("[data-viewer-count]").textContent = `${index + 1} / ${thumbs.length}`;
  thumbs.forEach((thumb, k) => thumb.setAttribute("aria-current", String(k === index)));
  thumbs[index].scrollIntoView({ block: "nearest", inline: "nearest" });

  const isImage = file.kind === "image";
  image.hidden = !isImage;
  pdf.hidden = isImage;
  zoomGroup.hidden = !isImage;
  if (isImage) {
    pdf.removeAttribute("src");
    image.src = file.src;
    image.alt = file.name;
    setZoom(null);
  } else {
    image.removeAttribute("src");
    pdf.src = file.src;
  }
}

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-view]");
  // Cmd/Ctrl-click or a middle click still opens the file in a new tab, as people expect.
  if (link === null || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
  const at = thumbs.findIndex((thumb) => thumb.dataset.id === link.dataset.view);
  if (at === -1) return;
  event.preventDefault();
  viewer.showModal();
  show(at);
});

viewer.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (button === null) return;
  if (button.matches("[data-viewer-close]")) viewer.close();
  if (button.dataset.viewerStep) show(index + Number(button.dataset.viewerStep));
  if (button.matches("[data-viewer-thumb]")) show(thumbs.indexOf(button));
  if (button.dataset.zoom === "fit") setZoom(null);
  else if (button.dataset.zoom) setZoom((zoom ?? 100) + Number(button.dataset.zoom));
});

// Keys the viewer handles do only that: an arrow key must not also scroll a zoomed image.
const KEYS = {
  ArrowLeft: () => show(index - 1),
  ArrowRight: () => show(index + 1),
  "+": () => setZoom((zoom ?? 100) + 25),
  "=": () => setZoom((zoom ?? 100) + 25),
  "-": () => setZoom((zoom ?? 100) - 25),
  0: () => setZoom(null),
};
const ARROWS = ["ArrowLeft", "ArrowRight"];

viewer.addEventListener("keydown", (event) => {
  const handle = KEYS[event.key];
  // Zoom keys mean nothing for a PDF, which has its own zoom.
  if (handle === undefined || (image.hidden && !ARROWS.includes(event.key))) return;
  event.preventDefault();
  handle();
});

// Esc closes the dialog by itself; either way the PDF frame stops loading in the background.
viewer.addEventListener("close", () => {
  pdf.removeAttribute("src");
  image.removeAttribute("src");
});
