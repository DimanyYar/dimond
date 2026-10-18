// Search as you type: a short pause after the last key fetches the page for that search and
// swaps in only the results, so the page neither reloads nor jumps to the top. The address
// follows, so a search can be shared or reloaded. Without this script Enter still works.
const form = document.querySelector("[data-live-search]");
const input = form?.querySelector('input[name="q"]');
const results = document.getElementById("request-results");
const PAUSE_MS = 300;

if (form && input && results) {
  let timer = 0;
  let latest = 0;

  async function show(query) {
    const url = new URL(location.href);
    if (query) url.searchParams.set("q", query);
    else url.searchParams.delete("q");
    // Fast typing sends several requests; only the answer to the newest one may win.
    const asked = ++latest;
    const response = await fetch(url);
    if (!response.ok) return;
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    const fresh = page.getElementById("request-results");
    if (asked !== latest || !fresh) return;
    results.replaceChildren(...fresh.childNodes);
    history.replaceState(null, "", url);
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
  results.addEventListener("click", (event) => {
    if (!event.target.closest("[data-clear-search]")) return;
    event.preventDefault();
    input.value = "";
    input.focus();
    show("");
  });
}
