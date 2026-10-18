// The files picker: files chosen, dropped or pasted (Cmd+V) are kept in the form's file
// input and shown as tiles (form-tile.js), and each can be removed before sending. A video is first made ready for
// upload (compress-video.js) and joins the input only then. The server checks every file
// again; the checks here only save a round trip.

import { VideoProblem, readyVideo } from "./compress-video.js";
import { formTile } from "./form-tile.js";
import { watchRecorders } from "./record-media.js";

const MAX_FILES = 5;
const MAX_SIZE = 10 * 1024 * 1024;

// A pasted screenshot arrives as "image.png"; five of those in a request are hard to tell apart.
function named(file) {
  if (file.name !== "image.png") return file;
  const time = new Date().toTimeString().slice(0, 8).replaceAll(":", "-");
  return new File([file], `screenshot-${time}.png`, { type: file.type });
}

export function watchFiles(form) {
  const box = form.querySelector("[data-attach]");
  if (box === null) return;
  const input = box.querySelector("[data-attach-input]");
  const posters = box.querySelector("[data-attach-posters]");
  const list = box.querySelector("[data-attach-list]");
  const error = box.querySelector("[data-attach-error]");
  // Files already in the input: carried over from a form the server sent back with errors.
  // A video being compressed is listed with its progress and kept out of the input until done.
  let files = [...input.files].map((file) => ({ file, progress: null, framesPending: 0, stop: null, tile: null }));
  box.classList.add("is-enhanced");
  input.classList.add("visually-hidden");

  function tileFor(entry) {
    entry.tile ??= formTile(entry.file, () => {
      entry.stop?.abort();
      entry.tile.forget();
      files = files.filter((kept) => kept !== entry);
      error.textContent = "";
      show();
      input.focus();
    });
    return entry.tile.element;
  }

  function show() {
    const transfer = new DataTransfer();
    files.filter((entry) => entry.progress === null).forEach((entry) => transfer.items.add(entry.file));
    input.files = transfer.files;
    list.replaceChildren(...files.map(tileFor));
    sendFrames();
  }

  // Each ready video's frame goes with the form named after the video's place among the files
  // sent ("2.jpg" for the third), so the server knows whose frame it is (apps/files/forms.py).
  function sendFrames() {
    const transfer = new DataTransfer();
    files
      .filter((entry) => entry.progress === null)
      .forEach((entry, place) => {
        const frame = entry.tile.frame();
        if (frame !== null) transfer.items.add(new File([frame], `${place}.jpg`, { type: "image/jpeg" }));
      });
    posters.files = transfer.files;
  }

  // Sending before a frame arrives would send the video without it, or with the frame of the
  // file before compressing.
  function takeFrame(entry, video) {
    entry.framesPending += 1;
    entry.tile
      .showFrame(video)
      .then(sendFrames)
      .finally(() => {
        entry.framesPending -= 1;
      });
  }

  async function compress(entry) {
    try {
      const ready = await readyVideo(entry.file, {
        signal: entry.stop.signal,
        // Only the bar moves: rebuilding the list would take focus off a × button.
        onProgress: (done) => {
          entry.progress = done;
          entry.tile.progress(done);
        },
      });
      entry.file = ready;
      entry.progress = null;
      entry.tile.replaceFile(ready);
      entry.tile.progress(null);
      // The frame of what gets uploaded; a browser that could not show the original (HEVC in
      // Chrome) can show this one.
      takeFrame(entry, ready);
    } catch (problem) {
      // Removed by the client while compressing: it is already gone from the list.
      if (entry.stop.signal.aborted) return;
      entry.tile.forget();
      files = files.filter((kept) => kept !== entry);
      error.textContent =
        problem instanceof VideoProblem
          ? problem.message
          : `${entry.file.name}: this browser can’t compress this video. Share a Google Drive, Loom or YouTube link instead.`;
    }
    entry.stop = null;
    show();
  }

  function add(added) {
    const problems = [];
    for (const file of added) {
      if (files.length === MAX_FILES) {
        problems.push(`Attach up to ${MAX_FILES} files at a time.`);
        break;
      }
      // A video's size is judged after compressing, which is what gets uploaded.
      const isVideo = file.type.startsWith("video/");
      if (!isVideo && file.size > MAX_SIZE) {
        problems.push(`${file.name} is over 10 MB. Make it smaller or share a link instead.`);
        continue;
      }
      const entry = { file: named(file), progress: null, framesPending: 0, stop: null, tile: null };
      files.push(entry);
      if (isVideo) {
        entry.progress = 0;
        entry.stop = new AbortController();
        tileFor(entry);
        entry.tile.progress(0);
        takeFrame(entry, entry.file);
        compress(entry);
      }
    }
    error.textContent = problems.join(" ");
    show();
  }

  const recorders = watchRecorders(box, {
    add,
    hasRoom: () => files.length < MAX_FILES,
    showError: (text) => {
      error.textContent = text;
    },
  });

  // Sending while recording or compressing would send the form without that file. Registered
  // before the form's own submit handler, so it can stop that one too.
  form.addEventListener("submit", (event) => {
    const recording = recorders.isRecording();
    const busy = files.some((entry) => entry.progress !== null || entry.framesPending > 0);
    if (!recording && !busy) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    error.textContent = recording ? "Stop the recording first, then send." : "Wait until the video is ready, then send.";
  });

  // Picking in the dialog replaces the input's files; the earlier ones are kept in `files`.
  input.addEventListener("change", () => add([...input.files]));

  document.addEventListener("paste", (event) => {
    const pasted = [...(event.clipboardData?.files ?? [])];
    if (pasted.length === 0 || !document.contains(box)) return;
    event.preventDefault();
    add(pasted);
  });

  box.addEventListener("dragover", (event) => {
    event.preventDefault();
    box.classList.add("is-dragging");
  });
  box.addEventListener("dragleave", () => box.classList.remove("is-dragging"));
  box.addEventListener("drop", (event) => {
    event.preventDefault();
    box.classList.remove("is-dragging");
    add([...event.dataTransfer.files]);
  });

  // Files handed over by another part of the page: "Just say it" passes its recording when
  // the client switches to the full form.
  form.addEventListener("attach-files", (event) => add(event.detail));

  form.addEventListener("reset", () => {
    files.forEach((entry) => {
      entry.stop?.abort();
      entry.tile?.forget();
    });
    files = [];
    error.textContent = "";
    list.replaceChildren();
  });

  show();
}
