// Filters and search change only the address's query. Instead of reloading, the page for the
// new address is fetched and the parts that depend on it (marked data-region) are swapped in,
// so scroll position and focus stay put.
let latest = 0;

// Which swap the page is on. Anything that started before a newer swap must not touch the
// page: its answer belongs to filters that are gone.
export function pageGeneration() {
  return latest;
}

// Returns whether this swap reached the page; an older one overtaken by a newer click did not.
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
    // Offline or a server error: an ordinary page load shows what really happened, unless
    // a newer click already moved on.
    if (asked === latest) location.assign(url);
    return false;
  }
  if (asked !== latest) return false;
  // A signed-out session answers with the sign-in page: load it for real.
  if (!page.querySelector('[data-region="results"]')) {
    location.assign(url);
    return false;
  }
  const scrolled = window.scrollY;
  for (const region of document.querySelectorAll("[data-region]")) {
    const fresh = page.querySelector(`[data-region="${region.dataset.region}"]`);
    if (fresh) region.replaceChildren(...fresh.childNodes);
  }
  holdScroll(results, scrolled);
  if (push) history.pushState(null, "", url);
  else history.replaceState(null, "", url);
  results?.removeAttribute("aria-busy");
  return true;
}

// A shorter list makes the page shorter than where the person scrolled to, and the browser
// pulls everything up. Room under the list keeps the filters where they were on screen; each
// swap measures again, so the room goes once the list is long enough.
function holdScroll(results, scrolled) {
  results.style.minHeight = "";
  const missing = scrolled + window.innerHeight - document.documentElement.scrollHeight;
  if (missing > 0) results.style.minHeight = `${results.offsetHeight + missing}px`;
  window.scrollTo({ top: scrolled, behavior: "instant" });
}
