// Search as you type: a short pause after the last key shows the results for that search
// without a reload (swap-page.js). The address follows, so a search can be shared or
// reloaded. Without this script Enter still works.
import { showPage } from "./swap-page.js";

const form = document.querySelector("[data-live-search]");
const input = form?.querySelector('input[name="q"]');
const PAUSE_MS = 300;

if (form && input) {
  let timer = 0;

  function show(query) {
    const url = new URL(location.href);
    if (query) url.searchParams.set("q", query);
    else url.searchParams.delete("q");
    // Typing is one search, not a history entry per letter.
    showPage(url, { push: false });
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => show(input.value.trim()), PAUSE_MS);
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    clearTimeout(timer);
    show(input.value.trim());
  });

  // "Clear" under the results empties the box too, not just the list.
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-clear-search]")) return;
    event.preventDefault();
    input.value = "";
    input.focus();
    show("");
  });
}
