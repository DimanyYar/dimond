// The attachment viewer: images, PDFs, video and audio open over the card instead of in a new
// tab, with zoom for images, our own players for video and voice notes (media-player.js),
// arrows and thumbnails to go through every file. Without this script the same links still
// open each file in a new tab.

import { closeSpeedMenus, mediaPlayer } from "./media-player.js";

const viewer = document.getElementById("media-viewer");
const thumbs = [...viewer.querySelectorAll("[data-viewer-thumb]")];
const stage = viewer.querySelector("[data-viewer-stage]");
const image = viewer.querySelector("[data-viewer-image]");
const pdf = viewer.querySelector("[data-viewer-pdf]");
const videoBox = viewer.querySelector('[data-player="video"]');
const audioBox = viewer.querySelector('[data-player="audio"]');
const video = mediaPlayer(videoBox);
const audio = mediaPlayer(audioBox);
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

  // Only one file plays at a time: whatever was on screen stops loading and playing.
  stop();
  image.hidden = file.kind !== "image";
  pdf.hidden = file.kind !== "pdf";
  videoBox.hidden = file.kind !== "video";
  audioBox.hidden = file.kind !== "audio";
  zoomGroup.hidden = file.kind !== "image";
  if (file.kind === "image") {
    image.src = file.src;
    image.alt = file.name;
    setZoom(null);
  } else if (file.kind === "pdf") {
    pdf.src = file.src;
  } else if (file.kind === "video") {
    video.load(file.src);
  } else {
    audio.load(file.src, { by: file.by, when: file.when });
  }
}

function stop() {
  image.removeAttribute("src");
  pdf.removeAttribute("src");
  video.stop();
  audio.stop();
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
  // A focused slider seeks with the arrow keys, and the speed list moves through its choices.
  if (event.target.matches("[data-seek], [role='menuitemradio']")) return;
  // Space plays and pauses, as in any player; on a focused button it already presses that button.
  if (event.key === " " && !event.target.matches("button, a")) {
    const player = { video, audio }[thumbs[index].dataset.kind];
    if (player === undefined) return;
    event.preventDefault();
    player.toggle();
    return;
  }
  const handle = KEYS[event.key];
  // Zoom keys mean nothing for a PDF, which has its own zoom.
  if (handle === undefined || (image.hidden && !ARROWS.includes(event.key))) return;
  event.preventDefault();
  handle();
});

// Esc with the speed list open closes only the list.
viewer.addEventListener("cancel", (event) => {
  if (closeSpeedMenus()) event.preventDefault();
});

// Esc closes the dialog by itself; either way nothing keeps loading or playing in the background.
viewer.addEventListener("close", stop);
