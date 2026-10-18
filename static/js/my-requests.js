import { pageGeneration } from "./swap-page.js";

// The phone filter sheet: while the client picks types and a period, the button says how many
// requests they will get ("Show 3 requests") before the filters are applied.
const form = document.querySelector("[data-filter-sheet]");

if (form) {
  let latest = 0;
  form.addEventListener("change", async () => {
    // Quick taps send several requests; only the answer to the newest one may win.
    const asked = ++latest;
    const generation = pageGeneration();
    const query = new URLSearchParams(new FormData(form));
    const response = await fetch(`${form.dataset.countUrl}?${query}`);
    if (!response.ok) return;
    const text = await response.text();
    // Checked after the body arrives: a newer tap may have come in while it was loading.
    // The label is looked up now: a filter change without reload replaces the sheet's contents.
    const label = form.querySelector("[data-show-count]");
    if (asked === latest && generation === pageGeneration() && label) {
      label.textContent = text.trim();
    }
  });
}
