// The arrow on a task row shows or hides its subtask rows, which sit right under it in the same
// row group (Releases page, 30c). Delegated, so rows swapped in by filters.js work too.
document.addEventListener("click", (event) => {
  const toggle = event.target.closest("button[data-subtasks]");
  if (!toggle) return;
  const open = toggle.getAttribute("aria-expanded") !== "true";
  toggle.setAttribute("aria-expanded", String(open));
  toggle.setAttribute("aria-label", toggle.getAttribute("aria-label").replace(open ? /^Show/ : /^Hide/, open ? "Hide" : "Show"));
  for (const row of toggle.closest("tbody").querySelectorAll(".subtask-row")) row.hidden = !open;
});
