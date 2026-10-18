# Overview media

The two-minute video and supporting artwork. These are documentation
assets, not runtime dependencies; no submission records or rendering caches are
included.

The project READMEs and overview pages use a native video player, with the
video's opening frame as its preview. The source URL is pinned to immutable
commit `894cb4f1032f7411fb3faa791a3b9c3782a58a71`, where the identical MP4 is stored;
the small time fragment loads the opening image rather than a separate poster.
GitHub strips custom `poster` attributes, so no hand-written cover image is
used as a substitute for playback. Local Markdown readers can use the download link.

| Asset | Purpose |
| --- | --- |
| [PNG](contexture-overview.png) / [editable SVG](contexture-overview.svg) | Supporting diagram for the design guide; not a README cover |
| [MP4](contexture-overview.mp4?raw=1) | 119-second English overview, 1920×1080, H.264/AAC |
| [English subtitles](contexture-overview.en.srt) | Phrase-level captions timed to speech boundaries; also burned into the video |
| [Chinese subtitles](contexture-overview.zh-CN.srt) | Paragraph-level translation track |

Read the [English overview and transcript](../overview.md) or
[Chinese version](../overview.zh-CN.md). The film illustrates implemented
mechanisms; it is not a recording of an autonomous model run. The runnable
Kubernetes demo is a separate example of the same framework.

The selected September 2026 video, PNG, and subtitles are reused unchanged.
Only trailing whitespace was cleaned in the editable SVG. Video SHA-256:
`ed93778897110329a372ee568929ff816adbe6e01ce28af8d39941917cb84de3`.
Cover PNG SHA-256:
`f8114f719e3a1a8e290ce3ad667d7854eec00b3027ba839e467ad6c40c898788`.

AI assistance: GitHub Copilot for presentation materials; Microsoft neural
voice for narration. No Microsoft endorsement is implied.
