// The PM's Collapse / Expand buttons (30e): the column narrows at once and the choice is saved
// in the background, so it stays the same on every page and device. Delegated, so the release
// list's button swapped in by filters.js works too. Without this script the form saves and
// reloads the page.
document.addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-collapse]");
  if (!form) return;
  event.preventDefault();
  const holder = form.closest(`[data-column="${form.dataset.collapse}"]`);
  const button = form.querySelector("button");
  const data = new FormData(form);
  const show = (collapsed) => {
    holder.toggleAttribute("data-collapsed", collapsed);
    form.elements.collapsed.value = collapsed ? "0" : "1";
    const label = collapsed ? button.dataset.expandLabel : button.dataset.collapseLabel;
    button.setAttribute("aria-label", label);
    button.title = label;
  };
  const collapsed = data.get("collapsed") === "1";
  show(collapsed);
  let saved = false;
  try {
    const response = await fetch(form.action, { method: "POST", body: data, headers: { "X-Requested-With": "fetch" } });
    saved = response.ok;
  } catch {
    saved = false;
  }
  // Not saved (offline, signed out): the column goes back, so the page never shows a width
  // that the next page would not keep.
  if (!saved) show(!collapsed);
});
