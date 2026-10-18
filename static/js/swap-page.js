// Filters and search change only the address's query. Instead of reloading, the page for the
// new address is fetched and the parts that depend on it (marked data-region) are swapped in,
// so scroll position and focus stay put.
let latest = 0;

export async function showPage(url, { push = true } = {}) {
  // Quick clicks or typing send several requests; only the answer to the newest one may win.
  const asked = ++latest;
  const results = document.querySelector('[data-region="results"]');
  results?.setAttribute("aria-busy", "true");
  let page;
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    page = new DOMParser().parseFromString(await response.text(), "text/html");
  } catch {
    // Offline or a server error: an ordinary page load shows what really happened.
    location.assign(url);
    return;
  }
  if (asked !== latest) return;
  for (const region of document.querySelectorAll("[data-region]")) {
    const fresh = page.querySelector(`[data-region="${region.dataset.region}"]`);
    if (fresh) region.replaceChildren(...fresh.childNodes);
  }
  if (push) history.pushState(null, "", url);
  else history.replaceState(null, "", url);
  results?.removeAttribute("aria-busy");
}
