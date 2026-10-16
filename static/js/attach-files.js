// The files picker: files chosen, dropped or pasted (Cmd+V) are kept in the form's file
// input and listed, and each can be removed before sending. The server checks every file
// again; the checks here only save a round trip.

const MAX_FILES = 5;
const MAX_SIZE = 10 * 1024 * 1024;
const MAX_VIDEO_SIZE = 20 * 1024 * 1024;

// A pasted screenshot arrives as "image.png"; five of those in a request are hard to tell apart.
function named(file) {
  if (file.name !== "image.png") return file;
  const time = new Date().toTimeString().slice(0, 8).replaceAll(":", "-");
  return new File([file], `screenshot-${time}.png`, { type: file.type });
}

export function watchFiles(form) {
  const box = form.querySelector("[data-attach]");
  if (box === null) return;
  const input = box.querySelector('input[type="file"]');
  const list = box.querySelector("[data-attach-list]");
  const error = box.querySelector("[data-attach-error]");
  // Files already in the input: carried over from a form the server sent back with errors.
  let files = [...input.files];
  box.classList.add("is-enhanced");
  input.classList.add("visually-hidden");

  function chip(file) {
    const item = document.createElement("li");
    item.className = "file-chip";
    const name = document.createElement("span");
    name.className = "file-chip-name";
    name.textContent = file.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "file-chip-remove";
    remove.setAttribute("aria-label", `Remove ${file.name}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      files = files.filter((kept) => kept !== file);
      error.textContent = "";
      show();
      input.focus();
    });
    item.append(name, remove);
    return item;
  }

  function show() {
    const transfer = new DataTransfer();
    files.forEach((file) => transfer.items.add(file));
    input.files = transfer.files;
    list.replaceChildren(...files.map(chip));
  }

  function add(added) {
    const problems = [];
    for (const file of added) {
      if (files.length === MAX_FILES) {
        problems.push(`Attach up to ${MAX_FILES} files at a time.`);
        break;
      }
      if (file.type.startsWith("video/") && file.size > MAX_VIDEO_SIZE) {
        problems.push(`${file.name} is over 20 MB. Share a link to Google Drive, Loom or YouTube instead.`);
        continue;
      }
      if (!file.type.startsWith("video/") && file.size > MAX_SIZE) {
        problems.push(`${file.name} is over 10 MB. Make it smaller or share a link instead.`);
        continue;
      }
      files.push(named(file));
    }
    error.textContent = problems.join(" ");
    show();
  }

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

  form.addEventListener("reset", () => {
    files = [];
    error.textContent = "";
    list.replaceChildren();
  });

  show();
}
