// "Just say it" (mockups: Just say it, Mobile just say it): the client records the request
// out loud, listens to it, gives it a title and sends it. The recording is the request; the
// type is optional and the PM sorts the rest. Buttons that open it stay hidden where the
// browser cannot record, so the page then offers only the form.

import { clock } from "./clock.js";
import { MAX_DURATION, finishedRecording } from "./compress-video.js";
import { mediaPlayer } from "./media-player.js";

const dialog = document.getElementById("voice-request");
const form = dialog.querySelector("[data-voice-form]");
const error = dialog.querySelector("[data-voice-error]");
const time = dialog.querySelector("[data-voice-time]");
const level = dialog.querySelector("[data-voice-level]");
const player = mediaPlayer(dialog.querySelector("[data-voice-player]"));
const LEVEL_BARS = 48;
const TYPES = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg"];
const EXTENSIONS = { "audio/webm": "webm", "audio/mp4": "m4a", "audio/ogg": "ogg" };
const COPY = {
  ready: ["Just say it", "Describe your request out loud. We’ll keep the recording for Dimon."],
  recording: ["Listening…", "Talk like you would to Dimon on a call."],
  review: ["Looks right?", "Listen back, give it a title and send it — or record again."],
};

const canRecord = "MediaRecorder" in window && typeof navigator.mediaDevices?.getUserMedia === "function";
let recorder = null;
let stream = null;
let recording = null;
let ticking = null;
let listening = null;

function stage(name) {
  dialog.dataset.stage = name;
  dialog.querySelectorAll("[data-voice-stage]").forEach((part) => {
    part.hidden = part.dataset.voiceStage !== name;
  });
  dialog.querySelector("[data-voice-title]").textContent = COPY[name][0];
  dialog.querySelector("[data-voice-subtitle]").textContent = COPY[name][1];
}

// The bars move with the voice, so the client sees the microphone hears them.
function listen() {
  const context = new AudioContext();
  const analyser = context.createAnalyser();
  analyser.fftSize = 512;
  context.createMediaStreamSource(stream).connect(analyser);
  const samples = new Uint8Array(analyser.fftSize);
  const heights = Array(LEVEL_BARS).fill(0.1);
  level.replaceChildren(...heights.map(() => document.createElement("span")));
  let frame;
  function draw() {
    analyser.getByteTimeDomainData(samples);
    let loudest = 0;
    for (const sample of samples) loudest = Math.max(loudest, Math.abs(sample - 128) / 128);
    heights.shift();
    heights.push(Math.min(1, 0.1 + loudest * 1.6));
    [...level.children].forEach((bar, k) => {
      bar.style.height = `${Math.round(heights[k] * 100)}%`;
    });
    frame = requestAnimationFrame(draw);
  }
  draw();
  return () => {
    cancelAnimationFrame(frame);
    context.close();
  };
}

function release() {
  clearInterval(ticking);
  listening?.();
  listening = null;
  stream?.getTracks().forEach((track) => track.stop());
  stream = null;
}

async function start() {
  error.textContent = "";
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (problem) {
    stage("ready");
    dialog.querySelector(".voice-hint").textContent =
      problem.name === "NotAllowedError"
        ? "Allow the microphone for this site, then tap again."
        : "No microphone found. Use the full form instead.";
    return;
  }
  const type = TYPES.find((candidate) => MediaRecorder.isTypeSupported(candidate)) ?? "";
  const chunks = [];
  recorder = new MediaRecorder(stream, { mimeType: type, audioBitsPerSecond: 96_000 });
  recorder.addEventListener("dataavailable", (event) => chunks.push(event.data));
  recorder.addEventListener("stop", async () => {
    release();
    const recordedType = recorder.mimeType.split(";")[0];
    const stamp = new Date().toTimeString().slice(0, 8).replaceAll(":", "-");
    const name = `voice-request-${stamp}.${EXTENSIONS[recordedType] ?? "webm"}`;
    const recorded = new Blob(chunks, { type: recorder.mimeType });
    recorder = null;
    // Cancelled while recording: the window is closed and nothing is kept.
    if (!dialog.open) return;
    try {
      recording = await finishedRecording(recorded, name);
    } catch {
      // Better a recording without seeking than none: it still plays from the start.
      recording = new File([recorded], name, { type: recordedType });
    }
    stage("review");
    player.load(URL.createObjectURL(recording));
    form.elements.title.focus();
  });
  recorder.start(1000);
  const startedAt = performance.now();
  time.textContent = "0:00";
  ticking = setInterval(() => {
    const seconds = (performance.now() - startedAt) / 1000;
    time.textContent = clock(seconds);
    if (seconds >= MAX_DURATION) stop();
  }, 250);
  listening = listen();
  stage("recording");
  dialog.querySelector(".voice-stop").focus();
}

function stop() {
  if (recorder?.state === "recording") recorder.stop();
}

function close() {
  stop();
  release();
  player.stop();
  dialog.close();
}

function open() {
  recording = null;
  form.reset();
  error.textContent = "";
  dialog.querySelector(".voice-hint").textContent = "Tap to start · up to 2 minutes";
  stage("ready");
  dialog.showModal();
}

// Picking the chosen type again leaves the type open, as "optional" promises.
let typeBefore = null;
form.querySelectorAll('input[name="type"]').forEach((radio) => {
  radio.addEventListener("pointerdown", () => {
    typeBefore = radio.checked ? radio : null;
  });
  radio.addEventListener("click", () => {
    if (typeBefore === radio) radio.checked = false;
    typeBefore = null;
  });
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  const send = form.querySelector("[data-voice-send]");
  const label = send.textContent;
  send.disabled = true;
  send.textContent = "Sending…";
  error.textContent = "";
  const data = new FormData(form);
  data.set("recording", recording, recording.name);
  let response;
  try {
    response = await fetch(form.action, { method: "POST", body: data });
  } catch {
    response = null;
  }
  if (response?.status === 201) {
    window.location.assign(response.headers.get("Location"));
    return;
  }
  send.disabled = false;
  send.textContent = label;
  if (response?.status === 422) {
    error.textContent = (await response.json()).errors.join(" ");
  } else {
    error.textContent = "Couldn’t reach dimon.d. Check your connection and try again.";
  }
});

dialog.querySelector("[data-voice-start]").addEventListener("click", start);
dialog.querySelectorAll("[data-voice-stop]").forEach((button) => button.addEventListener("click", stop));
dialog.querySelectorAll("[data-voice-close]").forEach((button) => button.addEventListener("click", close));
dialog.querySelector("[data-voice-again]").addEventListener("click", () => {
  player.stop();
  start();
});
// Esc closes the window like Close does: recording stops and nothing is kept.
dialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  close();
});

document.querySelectorAll("[data-voice-open]").forEach((button) => {
  button.hidden = !canRecord;
  button.addEventListener("click", open);
});
