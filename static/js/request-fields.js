// The request fields' live copy, shared by the new request and the edit forms: labels,
// placeholders and the urgency note follow the chosen type and urgency without a reload.

const typeCopy = JSON.parse(document.getElementById("type-copy").textContent);
const whenNeededCopy = JSON.parse(document.getElementById("when-needed-copy").textContent);

export function applyType(form, type) {
  const copy = typeCopy[type];
  form.querySelectorAll("[data-copy]").forEach((el) => {
    el.textContent = copy[el.dataset.copy];
  });
  form.querySelectorAll("[data-copy-placeholder]").forEach((el) => {
    el.placeholder = copy[el.dataset.copyPlaceholder];
  });
  form.elements.link.required = type === "bug";
}

export function applyWhenNeeded(form) {
  const value = form.elements.when_needed.value;
  form.querySelector("[data-when-note]").textContent = whenNeededCopy[value].note;
}

export function watchFields(form) {
  form.addEventListener("change", (event) => {
    if (event.target.name === "type") applyType(form, event.target.value);
    if (event.target.name === "when_needed") applyWhenNeeded(form);
  });
}
