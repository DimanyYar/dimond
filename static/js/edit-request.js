// Edit request form: the live field copy and the files picker; saving is a plain POST back
// to the card.

import { watchFiles } from "./attach-files.js";
import { applyType, applyWhenNeeded, watchFields } from "./request-fields.js";

const form = document.getElementById("edit-request-form");
watchFields(form);
watchFiles(form);
// Back to a page the browser kept may show a changed type without a change event.
window.addEventListener("pageshow", () => {
  applyType(form, form.elements.type.value);
  applyWhenNeeded(form);
});
