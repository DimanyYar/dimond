// A text draft of what the client says while "Just say it" records (mockup: Just say it,
// Recording, live transcript). It uses the browser's own speech recognition (Web Speech): the
// browser decides where the sound is recognised, which in Chrome means Google's servers. The
// recording is made separately and is always what Dimon gets; the text is only a draft.

const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition;
export const canTranscribe = Recognition !== undefined;

// Problems that will not go away by trying again: the draft stops, the recording goes on.
const GIVE_UP = ["not-allowed", "service-not-allowed", "audio-capture", "network", "language-not-supported"];
// After stop, the last words are recognised a moment later; past this the draft is sent as is.
const LAST_WORDS_WAIT = 2000;

function joined(...parts) {
  return parts.filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
}

// Starts listening. `onText(said, guessing)` gets the words so far: those the browser is sure of
// and those it may still change. `onFail` runs once if recognition cannot go on. `stop()`
// resolves to the final text; `abort()` drops it.
export function liveTranscript({ onText, onFail }) {
  const recognition = new Recognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  // The client talks in the language their browser is set to, not in the site's English.
  recognition.lang = navigator.language;

  // Chrome ends a session after a long pause even when told to go on, and starts a new list of
  // results; `earlier` keeps what the finished sessions heard.
  let earlier = "";
  let sure = "";
  let guessing = "";
  let listening = true;
  let finish;
  const finished = new Promise((resolve) => {
    finish = () => resolve(joined(earlier, sure, guessing));
  });

  recognition.addEventListener("result", (event) => {
    sure = "";
    guessing = "";
    for (const result of event.results) {
      if (result.isFinal) sure += ` ${result[0].transcript}`;
      else guessing += ` ${result[0].transcript}`;
    }
    onText(joined(earlier, sure), guessing.trim());
  });

  recognition.addEventListener("error", (event) => {
    if (!GIVE_UP.includes(event.error)) return;
    listening = false;
    onFail();
  });

  recognition.addEventListener("end", () => {
    earlier = joined(earlier, sure, guessing);
    sure = "";
    guessing = "";
    if (!listening) {
      finish();
      return;
    }
    recognition.start();
  });

  recognition.start();
  return {
    stop() {
      listening = false;
      recognition.stop();
      setTimeout(finish, LAST_WORDS_WAIT);
      return finished;
    },
    abort() {
      listening = false;
      recognition.abort();
    },
  };
}

// The title the review offers: the first sentence, cut at a word to fit one line.
export function firstSentence(text) {
  const sentence = text.split(/(?<=[.!?])\s/)[0].replace(/[.!?]+$/, "");
  if (sentence.length <= 80) return sentence.charAt(0).toUpperCase() + sentence.slice(1);
  const cut = sentence.slice(0, 80).replace(/\s+\S*$/, "");
  return `${cut.charAt(0).toUpperCase()}${cut.slice(1)}…`;
}
