// The viewer's players for video and voice notes (mockup: MediaViewer, Video and Audio): our
// own controls instead of each browser's, so they look the same in Chrome and Safari, and one
// playback speed holds for every file. Each control is optional: the video has a mute and a
// full-screen button, the voice note has its sound drawn as bars, and the "Just say it" check
// has no speed list.

import { clock } from "./clock.js";

const BAR_COUNT = 56;
const players = [];
let speed = 1;

function durationText(media) {
  return Number.isFinite(media.duration) ? clock(media.duration) : "–:––";
}

// The loudest moment in each slice of the sound, as a share of the loudest overall.
async function peaks(src, signal) {
  const response = await fetch(src, { signal });
  const sound = await new OfflineAudioContext(1, 1, 44100).decodeAudioData(await response.arrayBuffer());
  const samples = sound.getChannelData(0);
  const slice = Math.floor(samples.length / BAR_COUNT);
  const loudest = Array.from({ length: BAR_COUNT }, (_, bar) => {
    let peak = 0;
    for (let k = bar * slice; k < (bar + 1) * slice; k += 1) peak = Math.max(peak, Math.abs(samples[k]));
    return peak;
  });
  const top = Math.max(...loudest) || 1;
  return loudest.map((peak) => peak / top);
}

function setSpeed(value) {
  speed = value;
  for (const player of players) player.showSpeed();
}

export function closeSpeedMenus() {
  let closed = false;
  for (const player of players) closed = player.closeMenu() || closed;
  return closed;
}

export function mediaPlayer(root) {
  const media = root.querySelector("[data-media]");
  const playButtons = [...root.querySelectorAll("[data-play]")];
  const seek = root.querySelector("[data-seek]");
  const currents = [...root.querySelectorAll("[data-current]")];
  const durations = [...root.querySelectorAll("[data-duration]")];
  const bars = root.querySelector("[data-bars]");
  const mute = root.querySelector("[data-mute]");
  const fullscreen = root.querySelector("[data-fullscreen]");
  const speedButton = root.querySelector("[data-speed-button]");
  const speedMenu = root.querySelector("[data-speed-menu]");
  const speedItems = [...(speedMenu?.querySelectorAll("[data-speed]") ?? [])];
  let drawing = null;

  function showPlaying() {
    const playing = !media.paused;
    root.classList.toggle("is-playing", playing);
    for (const button of playButtons) {
      button.setAttribute("aria-label", playing ? "Pause" : "Play");
      button.querySelector("[data-icon-play]")?.toggleAttribute("hidden", playing);
      button.querySelector("[data-icon-pause]")?.toggleAttribute("hidden", !playing);
    }
  }

  function showTime() {
    const share = media.duration > 0 && Number.isFinite(media.duration) ? media.currentTime / media.duration : 0;
    for (const current of currents) current.textContent = clock(media.currentTime);
    seek.value = String(media.currentTime);
    // Read out instead of the bare number, as it changes: "0:12 of 0:47".
    seek.setAttribute("aria-valuetext", `${clock(media.currentTime)} of ${durationText(media)}`);
    seek.style.setProperty("--progress", `${share * 100}%`);
    if (bars !== null) {
      [...bars.children].forEach((bar, k) => bar.classList.toggle("is-played", k < Math.round(share * BAR_COUNT)));
    }
  }

  function showLength() {
    for (const duration of durations) duration.textContent = durationText(media);
    seek.max = Number.isFinite(media.duration) ? String(media.duration) : "0";
  }

  function drawBars(heights) {
    bars.replaceChildren(
      ...heights.map((height) => {
        const bar = document.createElement("span");
        // Never thinner than a dot, so silence still shows where the note is.
        bar.style.height = `${Math.max(8, Math.round(height * 100))}%`;
        return bar;
      }),
    );
    showTime();
  }

  async function drawSound(src) {
    drawing?.abort();
    drawing = new AbortController();
    const { signal } = drawing;
    drawBars(Array(BAR_COUNT).fill(0.15));
    try {
      const heights = await peaks(src, signal);
      // Decoding cannot be cancelled: a note left meanwhile must not draw over the next one.
      if (!signal.aborted) drawBars(heights);
    } catch {
      // Switched to another file, or the browser cannot decode this sound: the even bars stay
      // and still show progress and seek.
    }
  }

  function toggle() {
    if (media.paused) media.play();
    else media.pause();
  }

  function openMenu() {
    speedMenu.hidden = false;
    speedButton.setAttribute("aria-expanded", "true");
    (speedItems.find((item) => item.getAttribute("aria-checked") === "true") ?? speedItems[0]).focus();
  }

  function closeMenu() {
    if (speedMenu === null || speedMenu.hidden) return false;
    // A hidden item would drop the focus out of the viewer, and its keys with it.
    const hadFocus = speedMenu.contains(document.activeElement);
    speedMenu.hidden = true;
    speedButton.setAttribute("aria-expanded", "false");
    if (hadFocus) speedButton.focus();
    return true;
  }

  function showSpeed() {
    media.defaultPlaybackRate = speed;
    media.playbackRate = speed;
    if (speedButton === null) return;
    speedButton.querySelector("[data-speed-label]").textContent = `${speed}×`;
    for (const item of speedItems) item.setAttribute("aria-checked", String(Number(item.dataset.speed) === speed));
  }

  // The button that stays: the big one over a video disappears once it plays.
  const mainPlay = root.querySelector("[data-play]:not([data-big-play])");
  playButtons.forEach((button) => button.addEventListener("click", toggle));
  // Like any video on the web: a click on the picture plays and pauses. Focus moves to the
  // play button, so Space keeps working after the big button has gone.
  for (const target of [media, root.querySelector("[data-big-play]")]) {
    target?.addEventListener("click", () => mainPlay.focus({ preventScroll: true }));
  }
  if (media.matches("video")) media.addEventListener("click", toggle);
  media.addEventListener("play", showPlaying);
  media.addEventListener("pause", showPlaying);
  media.addEventListener("ended", showPlaying);
  media.addEventListener("timeupdate", showTime);
  media.addEventListener("loadedmetadata", () => {
    showLength();
    showTime();
  });
  media.addEventListener("durationchange", showLength);
  seek.addEventListener("input", () => {
    media.currentTime = Number(seek.value);
    showTime();
  });

  if (mute !== null) {
    mute.addEventListener("click", () => {
      media.muted = !media.muted;
      mute.setAttribute("aria-pressed", String(media.muted));
      mute.querySelector("[data-sound-wave]").toggleAttribute("hidden", media.muted);
    });
  }
  if (fullscreen !== null) {
    // iPhone has no full screen for a page element, only its own video player.
    fullscreen.hidden = !root.requestFullscreen && !media.webkitEnterFullscreen;
    fullscreen.addEventListener("click", () => {
      if (document.fullscreenElement) document.exitFullscreen();
      else if (root.requestFullscreen) root.requestFullscreen();
      else media.webkitEnterFullscreen();
    });
  }

  speedButton?.addEventListener("click", () => (speedMenu.hidden ? openMenu() : closeMenu()));
  speedItems.forEach((item, k) => {
    item.addEventListener("click", () => {
      setSpeed(Number(item.dataset.speed));
      closeMenu();
    });
    item.addEventListener("keydown", (event) => {
      const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
      if (step === undefined) return;
      event.preventDefault();
      event.stopPropagation();
      speedItems[(k + step + speedItems.length) % speedItems.length].focus();
    });
  });
  document.addEventListener("click", (event) => {
    if (speedMenu !== null && !speedMenu.hidden && !speedMenu.parentElement.contains(event.target)) closeMenu();
  });

  const player = {
    load(src, { by, when } = {}) {
      media.src = src;
      showSpeed();
      showPlaying();
      for (const current of currents) current.textContent = "0:00";
      for (const duration of durations) duration.textContent = "0:00";
      if (by !== undefined) {
        root.querySelector("[data-audio-initial]").textContent = by[0].toUpperCase();
        root.querySelector("[data-audio-by]").textContent = by;
        root.querySelector("[data-audio-when]").textContent = when;
      }
      if (bars !== null) drawSound(src);
    },
    stop() {
      drawing?.abort();
      closeMenu();
      media.pause();
      media.removeAttribute("src");
      media.load();
    },
    toggle,
    // On opening a file Space should play it at once, so the play button gets the focus.
    focus() {
      mainPlay.focus({ preventScroll: true });
    },
    closeMenu,
    showSpeed,
  };
  players.push(player);
  return player;
}
