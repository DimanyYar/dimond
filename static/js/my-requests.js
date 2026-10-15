// The phone filter sheet: while the client picks types and a period, the button says how many
// requests they will get ("Show 3 requests") before the filters are applied.
const form = document.querySelector("[data-filter-sheet]");
const label = form?.querySelector("[data-show-count]");

if (form && label) {
  let latest = 0;
  form.addEventListener("change", async () => {
    // Quick taps send several requests; only the answer to the newest one may win.
    const asked = ++latest;
    const query = new URLSearchParams(new FormData(form));
    const response = await fetch(`${form.dataset.countUrl}?${query}`);
    if (!response.ok) return;
    const text = await response.text();
    // Checked after the body arrives: a newer tap may have come in while it was loading.
    if (asked === latest) label.textContent = text.trim();
  });
}
