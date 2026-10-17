// Recording in the files picker: the screen with the client's voice over it, a voice note,
// or the phone's camera. Each button shows only where the browser can do it. A recording
// joins the list like a chosen file, so a screen recording is compressed like any video.

import { MAX_DURATION, finishedRecording } from "./compress-video.js";

const KINDS = {
  screen: {
    label: "Recording your screen",
    name: "screen-recording",
    types: ["video/webm;codecs=vp9,opus", "video/webm", "video/mp4"],
  },
  voice: {
    label: "Recording a voice note",
    name: "voice-note",
    types: ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg"],
  },
};
const EXTENSIONS = { "video/webm": "webm", "video/mp4": "mp4", "audio/webm": "webm", "audio/mp4": "m4a", "audio/ogg": "ogg" };

const devices = navigator.mediaDevices;
const SUPPORTED = {
  // Phones have no screen sharing in the browser; there the system's own recorder makes a
  // video the client attaches like any other.
  screen: "MediaRecorder" in window && typeof devices?.getDisplayMedia === "function",
  voice: "MediaRecorder" in window && typeof devices?.getUserMedia === "function",
  camera: window.matchMedia("(pointer: coarse)").matches,
};

function clock(seconds) {
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

// The screen first, as that is what the client asked for; then the microphone, which may be
// refused: a recording without a voice still shows the bug.
async function screenStream() {
  const screen = await devices.getDisplayMedia({ video: { frameRate: 30 }, audio: false });
  const voice = await devices.getUserMedia({ audio: true }).catch(() => null);
  return new MediaStream([...screen.getVideoTracks(), ...(voice?.getAudioTracks() ?? [])]);
}

function problemText(kind, problem) {
  // Cancelling the browser's "choose what to share" window is not a problem to report.
  if (kind === "screen" && problem.name === "NotAllowedError") return "";
  if (problem.name === "NotAllowedError") return "Allow the microphone for this site to record a voice note.";
  if (problem.name === "NotFoundError") return "No microphone found. Plug one in or attach a file instead.";
  return "Couldn’t start recording in this browser. Attach a file instead.";
}

export function watchRecorders(box, { add, hasRoom, showError }) {
  const actions = box.querySelector(".attach-actions");
  const bar = box.querySelector("[data-recording]");
  const label = bar.querySelector("[data-recording-label]");
  const time = bar.querySelector("[data-recording-time]");
  const stopButton = bar.querySelector("[data-recording-stop]");
  const camera = box.querySelector("[data-camera-input]");
  let recorder = null;
  // From the click until the file is in the list: the browser may still be asking for
  // permission, and a second click or a send must wait.
  let busy = false;

  box.querySelectorAll("[data-record]").forEach((button) => {
    button.hidden = !SUPPORTED[button.dataset.record];
    button.addEventListener("click", () => start(button.dataset.record));
  });
  box.querySelector("[data-camera]").hidden = !SUPPORTED.camera;
  box.querySelector("[data-camera]").addEventListener("click", () => camera.click());
  camera.addEventListener("change", () => {
    add([...camera.files]);
    camera.value = "";
  });

  async function start(kind) {
    if (busy) return;
    if (!hasRoom()) {
      showError("Attach up to 5 files at a time.");
      return;
    }
    showError("");
    busy = true;
    let stream;
    try {
      stream = kind === "screen" ? await screenStream() : await devices.getUserMedia({ audio: true });
    } catch (problem) {
      busy = false;
      showError(problemText(kind, problem));
      return;
    }
    const type = KINDS[kind].types.find((candidate) => MediaRecorder.isTypeSupported(candidate)) ?? "";
    const chunks = [];
    let ticking;
    try {
      // Screens are large and sharp; 2.5 Mbit/s keeps text readable before compression to 720p.
      recorder = new MediaRecorder(stream, { mimeType: type, videoBitsPerSecond: 2_500_000, audioBitsPerSecond: 96_000 });
      recorder.start(1000);
    } catch {
      stream.getTracks().forEach((track) => track.stop());
      recorder = null;
      busy = false;
      showError("Couldn’t start recording in this browser. Attach a file instead.");
      return;
    }
    const startedAt = performance.now();
    recorder.addEventListener("dataavailable", (event) => chunks.push(event.data));
    recorder.addEventListener("stop", async () => {
      clearInterval(ticking);
      stream.getTracks().forEach((track) => track.stop());
      label.textContent = "Finishing the recording…";
      time.textContent = "";
      stopButton.hidden = true;
      const recordedType = recorder.mimeType.split(";")[0];
      const stamp = new Date().toTimeString().slice(0, 8).replaceAll(":", "-");
      const name = `${KINDS[kind].name}-${stamp}.${EXTENSIONS[recordedType] ?? "webm"}`;
      const recorded = new Blob(chunks, { type: recorder.mimeType });
      let file;
      try {
        file = await finishedRecording(recorded, name);
      } catch {
        // Better a recording without seeking than no recording: it still plays from the start.
        file = new File([recorded], name, { type: recordedType });
      }
      recorder = null;
      bar.hidden = true;
      actions.hidden = false;
      stopButton.hidden = false;
      add([file]);
      busy = false;
    });
    // The browser's own "Stop sharing" button ends the screen track: that ends the recording too.
    stream.getVideoTracks().forEach((track) => track.addEventListener("ended", stop));

    ticking = setInterval(() => {
      const seconds = (performance.now() - startedAt) / 1000;
      time.textContent = `${clock(seconds)} / ${clock(MAX_DURATION)}`;
      if (seconds >= MAX_DURATION) stop();
    }, 250);
    label.textContent = KINDS[kind].label;
    time.textContent = `0:00 / ${clock(MAX_DURATION)}`;
    actions.hidden = true;
    bar.hidden = false;
    stopButton.focus();
  }

  function stop() {
    if (recorder?.state === "recording") recorder.stop();
  }

  stopButton.addEventListener("click", stop);
  return { isRecording: () => busy };
}
