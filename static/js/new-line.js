// Opens the card at the "new since you last opened it" line, so a long thread does not hide
// what changed. A link to a specific message (#message-12) keeps its own place.
const line = document.getElementById("new");
if (line && !location.hash) {
  line.scrollIntoView({ block: "center" });
}
