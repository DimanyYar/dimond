// Request card: the files picker in the reply box. Sending is a plain POST.

import { watchFiles } from "./attach-files.js";

watchFiles(document.getElementById("reply"));
