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
const LINE_MS = 1000;
const DONE_MS = 1200;
const LAST_SCENARIO_KEY = "dimond.lastSpinnerScenario";

const spinner = document.getElementById("spinner");

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

function startSpinner() {
  const lines = pickScenario();
  const card = spinner.querySelector(".spinner-card");
  const line = spinner.querySelector("[data-spinner-line]");
  const dots = spinner.querySelector("[data-spinner-dots]");
  dots.replaceChildren(...lines.map(() => document.createElement("span")));

  function show(index) {
    // A fresh node restarts the fade-in animation for every line.
    const fresh = line.cloneNode();
    fresh.textContent = lines[index];
    spinner.querySelector("[data-spinner-line]").replaceWith(fresh);
    [...dots.children].forEach((dot, k) => dot.classList.toggle("is-passed", k <= index));
  }

  card.classList.remove("is-done");
  spinner.hidden = false;
  show(0);
  const startedAt = Date.now();
  // A slow request holds on the middle line instead of announcing success too early.
  const middle = setTimeout(() => show(1), LINE_MS);

  return {
    stop() {
      clearTimeout(middle);
      spinner.hidden = true;
    },
    async finish() {
      // Even a fast server leaves the first line on screen long enough to read.
      await wait(Math.max(0, LINE_MS - (Date.now() - startedAt)));
      clearTimeout(middle);
      card.classList.add("is-done");
      show(lines.length - 1);
      await wait(DONE_MS);
    },
  };
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
    button.disabled = true;
    sendError.textContent = "";
    // A form the server will bounce gets no jokes: the errors show up straight away.
    const running = form.checkValidity() ? startSpinner() : null;

    let response;
    try {
      response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { "X-Requested-With": "fetch" },
      });
    } catch {
      running?.stop();
      sendError.textContent = "Couldn’t reach dimon.d. Check your connection and try again.";
      button.disabled = false;
      return;
    }

    if (response.status === 201) {
      await running?.finish();
      window.location.assign(response.headers.get("Location"));
      return;
    }

    running?.stop();
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    const withErrors = page.getElementById(form.id);
    if (response.status !== 422 || withErrors === null) {
      sendError.textContent = "Something went wrong on our side. Please try again in a minute.";
      button.disabled = false;
      return;
    }
    form.replaceWith(withErrors);
    bind(withErrors);
    withErrors.querySelector('[aria-invalid="true"]')?.focus();
  });
}

bind(document.getElementById("new-request-form"));

// Coming back with the Back button restores this page from cache, spinner and all.
window.addEventListener("pageshow", (event) => {
  if (!event.persisted) return;
  spinner.hidden = true;
  document.querySelector('#new-request-form [type="submit"]').disabled = false;
});
