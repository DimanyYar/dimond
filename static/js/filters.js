// Filter chips, tabs, period and view links, and the phone filter sheet show their result
// without a page reload (swap-page.js). Without this script they work as plain links.
import { showPage } from "./swap-page.js";

function isFilterLink(link) {
  const url = new URL(link.href);
  return (
    url.origin === location.origin &&
    url.pathname === location.pathname &&
    link.closest("[data-region]") !== null
  );
}

document.addEventListener("click", async (event) => {
  const link = event.target.closest("a[href]");
  const newTab = event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
  if (!link || event.defaultPrevented || newTab || !isFilterLink(link)) return;
  event.preventDefault();
  const region = link.closest("[data-region]");
  const position = [...region.querySelectorAll("a[href]")].indexOf(link);
  const hadFocus = document.activeElement === link;
  link.closest("[popover]")?.hidePopover();
  const shown = await showPage(link.href);
  // The clicked link was replaced and its focus fell back to the page; its successor takes it
  // so the keyboard stays here. If the person moved on meanwhile, their focus stays put.
  const focusLost = document.activeElement === document.body;
  if (shown && hadFocus && focusLost) region.querySelectorAll("a[href]")[position]?.focus({ preventScroll: true });
});

const sheet = document.querySelector("[data-filter-sheet]");
sheet?.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = new URLSearchParams(new FormData(sheet));
  sheet.closest("[popover]")?.hidePopover();
  showPage(`${location.pathname}?${query}`);
});

// Back and Forward walk through the filters the same way.
window.addEventListener("popstate", () => {
  showPage(location.href, { push: false });
  const search = document.getElementById("request-search");
  if (search) search.value = new URL(location.href).searchParams.get("q") ?? "";
});
