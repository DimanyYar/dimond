// "Just say it" (mockups: Just say it, Mobile just say it): the client records the request
// out loud, listens to it, gives it a title and sends it. The recording is the request; the
// type is optional and the PM sorts the rest. Where the browser can, a text draft is written
// while the client talks (live-transcript.js); the client checks it, and it goes with the
// recording as the request's text. The window lives over the new request form, so
// "Switch to the full form" hands the recording, title and type to it. Buttons and links that
// open it stay hidden where the browser cannot record, so the page then offers only the form.

import { clock } from "./clock.js";
import { MAX_DURATION, finishedRecording } from "./compress-video.js";
import { canTranscribe, firstSentence, liveTranscript } from "./live-transcript.js";
import { mediaPlayer } from "./media-player.js";

const LEVEL_BARS = 48;
const TYPES = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg"];
const EXTENSIONS = { "audio/webm": "webm", "audio/mp4": "m4a", "audio/ogg": "ogg" };
const COPY = {
  ready: [
    "Just say it",
    canTranscribe
      ? "Describe your request out loud. We’ll keep the recording and draft the text for you."
      : "Describe your request out loud. We’ll keep the recording for Dimon.",
  ],
  recording: ["Listening…", "Talk like you would to Dimon on a call."],
  review: ["Looks right?", "Listen back, give it a title and send it — or record again."],
};
const canRecord = "MediaRecorder" in window && typeof navigator.mediaDevices?.getUserMedia === "function";
const openers = document.querySelectorAll("[data-voice-open]");
openers.forEach((opener) => {
  opener.hidden = !canRecord;
});

const dialog = document.getElementById("voice-request");
// Pages without the window (My requests) only link to the form that has it.
if (dialog !== null) setUp();

function setUp() {
  const form = dialog.querySelector("[data-voice-form]");
  const error = dialog.querySelector("[data-voice-error]");
  const time = dialog.querySelector("[data-voice-time]");
  const level = dialog.querySelector("[data-voice-level]");
  const player = mediaPlayer(dialog.querySelector("[data-voice-player]"));
  const live = dialog.querySelector("[data-voice-transcript]");
  const heard = dialog.querySelector("[data-voice-heard]");

  let recorder = null;
  let stream = null;
  let recording = null;
  let ticking = null;
  let listening = null;
  // From the tap until recording begins the browser may be asking for the microphone.
  let starting = false;
  let recordingUrl = null;
  let transcript = null;

  function listenForText() {
    if (!canTranscribe) return;
    const said = live.querySelector("[data-voice-said]");
    const guess = live.querySelector("[data-voice-guess]");
    said.textContent = "";
    guess.textContent = "";
    live.hidden = false;
    transcript = liveTranscript({
      onText: (sure, guessing) => {
        said.textContent = sure;
        guess.textContent = guessing ? ` ${guessing}` : "";
      },
      // The recording goes on: the client just gets no draft this time.
      onFail: () => {
        live.hidden = true;
      },
    });
  }

  // The draft for the review: in the "What we heard" field, and its first sentence as the title
  // unless the client typed one already.
  function showText(text) {
    heard.hidden = text === "";
    form.elements.text.value = text;
    if (text && !form.elements.title.value) form.elements.title.value = firstSentence(text);
  }

  function dropText() {
    transcript?.abort();
    transcript = null;
    live.hidden = true;
  }

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

  // The player plays the recording from a temporary address; it is let go once not needed.
  function forgetRecording() {
    if (recordingUrl !== null) URL.revokeObjectURL(recordingUrl);
    recordingUrl = null;
  }

  async function start() {
    // A second tap while the browser asks for the microphone would open a second one.
    if (starting || recorder !== null) return;
    starting = true;
    error.textContent = "";
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (problem) {
      starting = false;
      stage("ready");
      dialog.querySelector(".voice-hint").textContent =
        problem.name === "NotAllowedError"
          ? "Allow the microphone for this site, then tap again."
          : "No microphone found. Use the full form instead.";
      return;
    }
    starting = false;
    // Closed while the browser was asking: the microphone goes off at once.
    if (!dialog.open) {
      release();
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
      const text = transcript === null ? "" : await transcript.stop();
      transcript = null;
      live.hidden = true;
      // Cancelled while recording: the window is closed and nothing is kept.
      if (!dialog.open) return;
      try {
        recording = await finishedRecording(recorded, name);
      } catch {
        // Better a recording without seeking than none: it still plays from the start.
        recording = new File([recorded], name, { type: recordedType });
      }
      stage("review");
      showText(text);
      forgetRecording();
      recordingUrl = URL.createObjectURL(recording);
      player.load(recordingUrl);
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
    listenForText();
    stage("recording");
    dialog.querySelector(".voice-stop").focus();
  }

  function stop() {
    if (recorder?.state === "recording") recorder.stop();
  }

  function close() {
    dropText();
    stop();
    release();
    player.stop();
    forgetRecording();
    dialog.close();
  }

  function open() {
    recording = null;
    form.reset();
    heard.hidden = true;
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
    showText("");
    player.stop();
    forgetRecording();
    start();
  });
  // Esc closes the window like Close does: recording stops and nothing is kept.
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    close();
  });

  // The full form gets everything said so far: the recording as a file, the title, the text
  // draft as the description, the type.
  dialog.querySelector("[data-voice-switch]").addEventListener("click", () => {
    const full = document.getElementById("new-request-form");
    const title = full.elements.title;
    if (!title.value) title.value = form.elements.title.value;
    const description = full.elements.description;
    if (!description.value) description.value = form.elements.text.value;
    const type = form.elements.type.value;
    const radio = type && full.querySelector(`input[name="type"][value="${type}"]`);
    if (radio) {
      radio.checked = true;
      radio.dispatchEvent(new Event("change", { bubbles: true }));
    }
    full.dispatchEvent(new CustomEvent("attach-files", { detail: [recording] }));
    close();
    (full.elements.why.value ? title : full.elements.why).focus();
  });

  // "Just say it" from another page arrives here with ?say: the window is open on arrival.
  const arrival = new URL(window.location.href);
  if (arrival.searchParams.has("say") && canRecord) {
    arrival.searchParams.delete("say");
    history.replaceState(history.state, "", arrival);
    open();
  }

  openers.forEach((opener) => opener.addEventListener("click", open));
}
