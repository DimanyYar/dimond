// Video is made ready for upload in the browser (PRD: access and security, attachments): up to
// 2 minutes, 720p, at most 20 MB, in a format every browser plays. A clip that already fits is
// sent as it is; anything else is re-encoded here with mediabunny (ADR 0012), so the client
// does not upload 100 MB from a phone and the PM can play it in any browser.

export const MAX_DURATION = 120;
export const MAX_VIDEO_SIZE = 20 * 1024 * 1024;
// The shorter side of 720p: 1280×720 landscape, 720×1280 portrait.
const SHORT_SIDE = 720;
// 1 Mbit/s of picture and 96 kbit/s of sound keep two minutes at about 16 MB.
const VIDEO_BITRATE = 1_000_000;
const AUDIO_BITRATE = 96_000;
// Codecs Chrome, Safari and Firefox all play. iPhone's HEVC is not on the list: Chrome on
// Windows often cannot play it.
const PLAYABLE = ["avc", "vp8", "vp9"];

export class VideoProblem extends Error {}

const linkHint = "Share a Google Drive, Loom or YouTube link instead.";

function compressionWorks() {
  return "VideoEncoder" in window && "VideoDecoder" in window;
}

// Loaded only when someone attaches a video: 180 KB the form does not need otherwise.
function library() {
  return import("../vendor/mediabunny/mediabunny.min.mjs");
}

function renamed(name, extension) {
  return `${name.replace(/\.[^.]+$/, "")}.${extension}`;
}

async function encoders(mb) {
  const video = await mb.getFirstEncodableVideoCodec(["avc", "vp9", "vp8"], {
    height: SHORT_SIDE,
    quality: new mb.Quality({ bitrate: VIDEO_BITRATE }),
  });
  if (video === null) return null;
  // H.264 goes into MP4, which Safari plays best; VP8 and VP9 go into WebM.
  const toMp4 = video === "avc";
  const audio = await mb.getFirstEncodableAudioCodec(toMp4 ? ["aac", "opus"] : ["opus"]);
  return { video, audio, toMp4 };
}

// The file to upload: `file` itself when it already fits, a smaller copy otherwise. Throws a
// VideoProblem with words for the client when the video cannot be sent. `signal` cancels it.
export async function readyVideo(file, { onProgress, signal }) {
  if (!compressionWorks()) {
    if (file.size <= MAX_VIDEO_SIZE) return file;
    throw new VideoProblem(`${file.name} is over 20 MB and this browser can’t compress video. ${linkHint}`);
  }
  const mb = await library();
  const input = new mb.Input({ source: new mb.BlobSource(file), formats: mb.ALL_FORMATS });
  const track = await input.getPrimaryVideoTrack();
  // A sound-only file chosen as video: the server checks and keeps it as audio.
  if (track === null) return file;
  if ((await input.computeDuration()) > MAX_DURATION + 0.5) {
    throw new VideoProblem(`${file.name} is over 2 minutes. ${linkHint}`);
  }
  const width = await track.getDisplayWidth();
  const height = await track.getDisplayHeight();
  const fits =
    file.size <= MAX_VIDEO_SIZE &&
    Math.min(width, height) <= SHORT_SIDE &&
    PLAYABLE.includes(await track.getCodec());
  if (fits) return file;

  const codecs = await encoders(mb);
  if (codecs === null) {
    if (file.size <= MAX_VIDEO_SIZE) return file;
    throw new VideoProblem(`${file.name} is over 20 MB and this browser can’t compress video. ${linkHint}`);
  }
  const target = new mb.BufferTarget();
  const output = new mb.Output({
    format: codecs.toMp4 ? new mb.Mp4OutputFormat({ fastStart: "in-memory" }) : new mb.WebMOutputFormat(),
    target,
  });
  // Only the shorter side is given, so the longer one keeps the proportions.
  const size = width >= height ? { height: Math.min(height, SHORT_SIDE) } : { width: Math.min(width, SHORT_SIDE) };
  const conversion = await mb.Conversion.init({
    input,
    output,
    video: {
      ...size,
      codec: codecs.video,
      quality: new mb.Quality({ bitrate: VIDEO_BITRATE }),
      forceTranscode: true,
    },
    audio: codecs.audio === null ? { discard: true } : { codec: codecs.audio, quality: new mb.Quality({ bitrate: AUDIO_BITRATE }) },
  });
  if (!conversion.utilizedTracks.some((used) => used.isVideoTrack())) {
    throw new VideoProblem(`${file.name}: this browser can’t read this video. ${linkHint}`);
  }
  conversion.onProgress = onProgress;
  // Removed while the library was still loading: no need to start.
  signal.throwIfAborted();
  signal.addEventListener("abort", () => conversion.cancel(), { once: true });
  await conversion.execute();

  const result = new File([target.buffer], renamed(file.name, codecs.toMp4 ? "mp4" : "webm"), {
    type: codecs.toMp4 ? "video/mp4" : "video/webm",
  });
  if (result.size > MAX_VIDEO_SIZE) {
    throw new VideoProblem(`${file.name} is still over 20 MB after compressing. ${linkHint}`);
  }
  return result;
}
