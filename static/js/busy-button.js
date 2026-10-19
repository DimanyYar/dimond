// A submit button that shows it is working while the server talks to Jira, which takes a few
// seconds: the button gets the spinner's orbit and the words from data-busy, and a second
// press does nothing. The form is sent the normal way, so it works without this script too.
for (const form of document.querySelectorAll("form[data-busy]")) {
  // The button may sit outside the form and join it by id, as in Existing task.
  const button =
    form.querySelector('button[type="submit"]') ??
    document.querySelector(`button[type="submit"][form="${form.id}"]`);
  const label = button.textContent;
  let sending = false;

  form.addEventListener("submit", (event) => {
    if (sending) {
      event.preventDefault();
      return;
    }
    sending = true;
    button.classList.add("is-busy");
    button.setAttribute("aria-disabled", "true");
    button.textContent = form.dataset.busy;
  });

  // Back from the next page restores this one from memory, still "busy": make it usable again.
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    sending = false;
    button.classList.remove("is-busy");
    button.removeAttribute("aria-disabled");
    button.textContent = label;
  });
}
