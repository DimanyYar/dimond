// Narrows a long list (a hundred Jira projects) to the options matching what is typed: a key
// or words from the name. Options are taken out of the list rather than hidden, because
// Safari on iPhone ignores hidden options. Without this script the box stays hidden and the
// full list works as it is.
for (const search of document.querySelectorAll("[data-filter-select]")) {
  const select = document.getElementById(search.dataset.filterSelect);
  const count = document.querySelector(`[data-filter-count="${select.id}"]`);
  const placeholder = select.querySelector('option[value=""]');
  const options = [...select.options].filter((option) => option.value !== "");
  const hint = count?.textContent ?? "";

  search.hidden = false;

  search.addEventListener("input", () => {
    const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const chosen = select.value;
    const shown = options.filter((option) =>
      words.every((word) => option.textContent.toLowerCase().includes(word)),
    );
    select.replaceChildren(placeholder, ...shown);
    // One match is the one meant; otherwise keep the earlier choice if it is still listed.
    select.value = shown.length === 1 ? shown[0].value : shown.some((o) => o.value === chosen) ? chosen : "";
    if (count) count.textContent = words.length ? `${shown.length} of ${options.length} projects` : hint;
  });
}
