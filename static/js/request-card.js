// Request card: the files pickers in the answer to the team and in the reply box. Sending is
// a plain POST.

import { watchFiles } from "./attach-files.js";

for (const id of ["answer", "reply"]) {
  const form = document.getElementById(id);
  if (form !== null) watchFiles(form);
}
