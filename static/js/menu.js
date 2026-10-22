// The phone menu: the ☰ in the top bar slides the sidebar in (mockups PM · phone, Client ·
// phone). Esc, the ×, or a tap outside closes it, and focus goes back to the ☰.
const app = document.querySelector("[data-menu]");
const button = app?.querySelector("[data-menu-open]");
const scrim = app?.querySelector(".menu-scrim");
// The page behind the open menu: inert, so Tab cannot walk out of the menu into it.
const behind = app ? [...app.querySelectorAll(".topbar, .main")] : [];
// Must match the phone breakpoint in dimond.css.
const phone = window.matchMedia("(max-width: 899px)");

function open() {
  app.setAttribute("data-menu-open", "");
  button.setAttribute("aria-expanded", "true");
  scrim.hidden = false;
  behind.forEach((el) => (el.inert = true));
  document.body.style.overflow = "hidden";
  app.querySelector(".sidebar .nav a")?.focus();
}

function close() {
  if (!app.hasAttribute("data-menu-open")) return;
  app.removeAttribute("data-menu-open");
  button.setAttribute("aria-expanded", "false");
  scrim.hidden = true;
  behind.forEach((el) => (el.inert = false));
  document.body.style.overflow = "";
  button.focus();
}

if (app && button && scrim) {
  button.addEventListener("click", open);
  app.querySelectorAll("[data-menu-close]").forEach((el) => el.addEventListener("click", close));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });
  // Widening the window past the phone layout hides the menu; without this the page would
  // stay unscrollable and inert.
  phone.addEventListener("change", (event) => {
    if (!event.matches) close();
  });
}
