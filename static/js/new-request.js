// New request form: the joke spinner while the request is sent, on top of the live field
// copy. Without this script the form still works as a plain POST.

import { applyType, applyWhenNeeded, watchFields } from "./request-fields.js";

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
// Survives a Back that reloads the page instead of restoring it from cache.
const SAVED_KEY = "dimond.requestSaved";
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function rememberSaved(saved) {
  try {
    const state = { ...(history.state ?? {}) };
    if (saved) state[SAVED_KEY] = true;
    else delete state[SAVED_KEY];
    history.replaceState(state, "");
  } catch {
    // Some browsers refuse; the in-memory flag still covers a cached Back, only a reloaded
    // Back loses it. A saved request must never get stuck on "Sending…" over this.
  }
}

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

function bind(form) {
  watchFields(form);

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
      rememberSaved(true);
      // Without the motion the jokes are only a pause, so people who turned it off go straight on.
      if (!reducedMotion.matches) await playSpinner();
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

// Coming back with the Back button after sending: the request is saved, so the client gets an
// empty form instead of one that would send it twice. The browser either restores the page
// from cache (spinner and all, maybe holding a form swapped in after an error) or reloads it
// and may fill the fields back in itself. An unsent draft is left as it was.
window.addEventListener("pageshow", (event) => {
  const form = document.getElementById("new-request-form");
  const savedInHistory = history.state?.[SAVED_KEY] === true;
  if (requestSaved || savedInHistory) {
    requestSaved = false;
    rememberSaved(false);
    spinner.hidden = true;
    if (event.persisted) {
      const fresh = emptyForm.cloneNode(true);
      form.replaceWith(fresh);
      bind(fresh);
      return;
    }
    form.reset();
  }
  // A restored draft keeps its type without a change event, so the labels catch up here.
  applyType(form, form.elements.type.value);
  applyWhenNeeded(form);
});
