// New request form: per-type copy, the urgency note, and the joke spinner while
// the request is sent. Without this script the form still works as a plain POST.

const typeCopy = JSON.parse(document.getElementById("type-copy").textContent);
const whenNeededCopy = JSON.parse(document.getElementById("when-needed-copy").textContent);

// One scenario per send; the last line is always the confirmation (mockups: Spinner scenarios).
const SCENARIOS = [
  ["Waking up the PM…", "PM says “five more minutes”.", "Snooze cancelled. Dimon is on it."],
  ["Brewing coffee for the PM…", "PM read your request before it was ready.", "Coffee’s cold. Request’s in."],
  ["Opening Jira…", "PM already has 47 tabs open. Closing 46.", "One tab left. It’s yours."],
  ["Booking a meeting about your request…", "Cancelled — it could have been a ticket.", "So it’s a ticket. Sent!"],
  ["Estimating your request…", "Multiplying by π, as is tradition.", "Saved. The π stays between us."],
  ["Translating your request into developer…", "“Make it pop” → “add 4 px of padding”.", "Translated and sent."],
  ["Looking for a spot in the backlog…", "Found one. Right at the top.", "Don’t tell the other tickets."],
  ["Pinging a developer…", "Developer: “works on my machine”.", "Your machine counts too. Request’s in."],
];
const LINE_MS = 1300;
const DONE_MS = 1000;
const LAST_SCENARIO_KEY = "dimond.lastSpinnerScenario";

const spinner = document.getElementById("spinner");
// The empty form as first served: after an error the form is swapped for one holding the
// typed values and error messages, and reset() would bring those back.
const emptyForm = document.getElementById("new-request-form").cloneNode(true);
let requestSaved = false;

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function pickScenario() {
  let last = -1;
  try {
    last = Number(sessionStorage.getItem(LAST_SCENARIO_KEY) ?? -1);
  } catch {
    // Storage can be blocked; then a repeat is merely possible, not a problem.
  }
  let index = Math.floor(Math.random() * SCENARIOS.length);
  if (index === last) index = (index + 1) % SCENARIOS.length;
  try {
    sessionStorage.setItem(LAST_SCENARIO_KEY, String(index));
  } catch {
    // Same as above.
  }
  return SCENARIOS[index];
}

// Plays only after the server has saved the request: a form it bounces must not flash
// a success animation first.
async function playSpinner() {
  const lines = pickScenario();
  const card = spinner.querySelector(".spinner-card");
  const dots = spinner.querySelector("[data-spinner-dots]");
  dots.replaceChildren(...lines.map(() => document.createElement("span")));

  function show(index) {
    // A fresh node restarts the fade-in animation for every line.
    const line = spinner.querySelector("[data-spinner-line]");
    const fresh = line.cloneNode();
    fresh.textContent = lines[index];
    line.replaceWith(fresh);
    [...dots.children].forEach((dot, k) => dot.classList.toggle("is-passed", k <= index));
  }

  card.classList.remove("is-done");
  spinner.hidden = false;
  for (let index = 0; index < lines.length - 1; index += 1) {
    show(index);
    await wait(LINE_MS);
  }
  card.classList.add("is-done");
  show(lines.length - 1);
  await wait(DONE_MS);
}

function applyType(form, type) {
  const copy = typeCopy[type];
  form.querySelectorAll("[data-copy]").forEach((el) => {
    el.textContent = copy[el.dataset.copy];
  });
  form.querySelectorAll("[data-copy-placeholder]").forEach((el) => {
    el.placeholder = copy[el.dataset.copyPlaceholder];
  });
  form.elements.link.required = type === "bug";
}

function applyWhenNeeded(form) {
  const value = form.elements.when_needed.value;
  form.querySelector("[data-when-note]").textContent = whenNeededCopy[value].note;
}

function bind(form) {
  form.addEventListener("change", (event) => {
    if (event.target.name === "type") applyType(form, event.target.value);
    if (event.target.name === "when_needed") applyWhenNeeded(form);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector('[type="submit"]');
    const sendError = form.querySelector("[data-send-error]");
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "Sending…";
    sendError.textContent = "";
    const restoreButton = () => {
      button.disabled = false;
      button.textContent = label;
    };

    let response;
    try {
      response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { "X-Requested-With": "fetch" },
      });
    } catch {
      sendError.textContent = "Couldn’t reach dimon.d. Check your connection and try again.";
      restoreButton();
      return;
    }

    if (response.status === 201) {
      requestSaved = true;
      await playSpinner();
      window.location.assign(response.headers.get("Location"));
      return;
    }

    let body;
    try {
      body = await response.text();
    } catch {
      sendError.textContent = "Couldn’t reach dimon.d. Check your connection and try again.";
      restoreButton();
      return;
    }
    const page = new DOMParser().parseFromString(body, "text/html");
    const withErrors = page.getElementById(form.id);
    if (response.status !== 422 || withErrors === null) {
      sendError.textContent = "Something went wrong on our side. Please try again in a minute.";
      restoreButton();
      return;
    }
    form.replaceWith(withErrors);
    bind(withErrors);
    withErrors.querySelector('[aria-invalid="true"]')?.focus();
  });
}

bind(document.getElementById("new-request-form"));

// Coming back with the Back button after sending restores this page from cache, spinner and
// all. The request is already saved, so the client gets an empty form instead of one that
// would send it twice. An unsent draft is left as it was.
window.addEventListener("pageshow", (event) => {
  if (!event.persisted || !requestSaved) return;
  requestSaved = false;
  spinner.hidden = true;
  const fresh = emptyForm.cloneNode(true);
  document.getElementById("new-request-form").replaceWith(fresh);
  bind(fresh);
});
