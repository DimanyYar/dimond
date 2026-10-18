// A chosen file as a tile in the form (mockup: Attachment previews, "In forms"), so the client
// sees what they pasted or picked before sending it: a screenshot is shown as it is, a video by
// one of its frames, a text file by its first lines. Built from the same pieces as the tiles
// on the card (templates/files/file_tile.html), so both look alike.

const BARS = [3, 7, 2, 8, 4, 6, 3, 1, 5, 2, 5];
const X_ICON =
  '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const PLAY_ICON =
  '<svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5l11 7-11 7z" fill="currentColor"/></svg>';
const FILE_ICON =
  '<svg class="tile-icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/></svg>';

function kindOf(file) {
  if (file.type === "application/pdf") return "pdf";
  const group = file.type.split("/")[0];
  if (["image", "video", "audio"].includes(group)) return group;
  // Some systems give .log, .csv and .m4a files no type at all.
  if (/\.m4a$/i.test(file.name)) return "audio";
  if (group === "text" || /\.(txt|log|csv)$/i.test(file.name)) return "text";
  return "file";
}

function extension(name) {
  return name.includes(".") ? name.split(".").pop().toUpperCase().slice(0, 4) : "FILE";
}

// The same words as the card's sizes, so a file reads "1.2 MB" before and after sending.
function size(bytes) {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function element(tag, className, text) {
  const made = document.createElement(tag);
  if (className) made.className = className;
  if (text !== undefined) made.textContent = text;
  return made;
}

function settled(media, event) {
  return new Promise((resolve, reject) => {
    media.addEventListener(event, resolve, { once: true });
    media.addEventListener("error", () => reject(media.error), { once: true });
  });
}

// A frame half a second in: the very first one is often black. Resolves to null where the
// browser cannot show this video (iPhone's HEVC in Chrome); the tile then shows a play button.
async function videoFrame(file) {
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.preload = "auto";
  const url = URL.createObjectURL(file);
  try {
    video.src = url;
    await settled(video, "loadeddata");
    if (video.videoWidth === 0) return null;
    video.currentTime = Math.min(0.5, video.duration / 2);
    await settled(video, "seeked");
    const scale = Math.min(1, 320 / video.videoWidth, 240 / video.videoHeight);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.8));
  } catch {
    return null;
  } finally {
    URL.revokeObjectURL(url);
  }
}

function playButton() {
  const play = element("span", "tile-play");
  play.innerHTML = PLAY_ICON;
  return play;
}

async function firstLines(file) {
  const text = await file.slice(0, 4096).text();
  return text
    .replace(/^﻿/, "")
    .split(/\r?\n/)
    .slice(0, 6)
    .map((line) => line.slice(0, 80))
    .join("\n");
}

// The tile for one chosen file. `onRemove` runs when × is pressed. Returns the element, a way
// to show compressing progress (a number from 0 to 1, or null when done) and `forget`, which
// lets go of the picture's temporary address once the tile is gone.
export function formTile(file, onRemove) {
  const kind = kindOf(file);
  const tile = element("li", "tile");
  tile.title = file.name;
  const picture = element("span", `tile-picture tile-${kind}`);
  const name = element("span", "tile-name", file.name);
  const meta = element("span", "tile-meta", size(file.size));
  const remove = element("button", "tile-remove");
  remove.type = "button";
  remove.setAttribute("aria-label", `Remove ${file.name}`);
  remove.innerHTML = X_ICON;
  remove.addEventListener("click", onRemove);
  tile.append(picture, name, meta, remove);

  let url = null;
  // A frame can arrive after × was pressed; a tile already gone takes no new address.
  let forgotten = false;
  function showPicture(blob) {
    if (blob === null || forgotten) return;
    if (url !== null) URL.revokeObjectURL(url);
    url = URL.createObjectURL(blob);
    const image = element("img");
    image.alt = "";
    image.src = url;
    // The compressing bar stays over the new picture.
    picture.querySelectorAll(":scope > :not(.tile-progress, .tile-percent)").forEach((part) => part.remove());
    picture.prepend(image);
    picture.classList.add("has-preview");
    if (kind === "video") image.after(playButton());
  }

  if (kind === "image") {
    showPicture(file);
  } else if (kind === "video") {
    picture.append(playButton());
  } else if (kind === "audio") {
    const bars = element("span", "tile-bars");
    bars.append(...BARS.map((height) => Object.assign(element("span"), { style: `height: ${height}0%` })));
    picture.append(bars);
  } else if (kind === "text") {
    const excerpt = element("span", "tile-excerpt");
    picture.append(excerpt, element("span", "tile-badge", extension(file.name)));
    firstLines(file).then((lines) => {
      excerpt.textContent = lines;
    });
  } else {
    picture.insertAdjacentHTML("beforeend", FILE_ICON);
    picture.append(element("span", "tile-ext", extension(file.name)));
    if (kind === "pdf") picture.append(element("span", "tile-badge tile-badge-pdf", "PDF"));
  }

  let framesAsked = 0;
  let frameShown = 0;

  let bar = null;
  let percent = null;
  function progress(done) {
    if (done === null) {
      bar?.parentElement.remove();
      percent?.remove();
      bar = percent = null;
      meta.textContent = size(file.size);
      return;
    }
    if (bar === null) {
      const track = element("span", "tile-progress");
      bar = element("span", "tile-progress-bar");
      track.append(bar);
      percent = element("span", "tile-percent");
      picture.append(track, percent);
      meta.textContent = "Compressing…";
    }
    bar.style.width = `${Math.round(done * 100)}%`;
    percent.textContent = `${Math.round(done * 100)}%`;
  }

  return {
    element: tile,
    progress,
    // A compressed video is a new file: its size is the one that gets uploaded.
    replaceFile(compressed) {
      file = compressed;
    },
    // Asked for the original video and again for the compressed one, the two frames can
    // arrive in either order: an older frame never covers a newer one.
    async showFrame(video) {
      const asked = ++framesAsked;
      const frame = await videoFrame(video);
      if (frame === null || asked < frameShown) return;
      frameShown = asked;
      showPicture(frame);
    },
    forget() {
      forgotten = true;
      if (url !== null) URL.revokeObjectURL(url);
      url = null;
    },
  };
}
