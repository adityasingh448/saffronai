# Saffron AI Sales Automation Agent

This MVP turns a prospect report PDF into a personalized walkthrough video:

1. Upload a PDF report.
2. Extract page text and render each page as an image.
3. Generate a consultative voiceover script.
4. Create natural voiceover audio with Deepgram when a key is configured.
5. Render a HyperFrames MP4 with page movement, report highlights, and no presenter/avatar branding.

## Run locally

```powershell
.\run.ps1
```

Open `http://127.0.0.1:8000`.

## Configure real AI services

Copy `.env.example` to `.env`, then add keys:

```powershell
Copy-Item .env.example .env
```

Required for AI script generation:

```env
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4.1-mini
```

Preferred for realistic report walkthrough voiceover:

```env
DEEPGRAM_API_KEY=your_key
DEEPGRAM_MODEL=aura-2-arcas-en
DEEPGRAM_SPEED=0.96
DEEPGRAM_MIP_OPT_OUT=true
```

Optional ElevenLabs fallback/custom voice:

```env
ELEVENLABS_API_KEY=your_key
ELEVENLABS_VOICE_ID=your_custom_voice_id
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
```

Without keys, the app still works in demo mode:

- Script generation uses a local deterministic writer.
- Voiceover uses Windows local speech when available. This is only a demo fallback; production-quality natural emotion requires Deepgram or another production TTS provider.

The upload form includes selectable Deepgram voices with play buttons. The selected voice model is stored on the job and used for the final video narration.

## Video rendering

HyperFrames is linked into the main agent pipeline by default:

```env
VIDEO_RENDERER=hyperframes
HYPERFRAMES_PACKAGE=hyperframes@0.7.22
HYPERFRAMES_QUALITY=high
HYPERFRAMES_FPS=60
```

Each job creates a fresh `hyperframes-render` project inside `data/jobs/<job-id>/`, copies the rendered PDF pages and voiceover into it, generates a dynamic `index.html`, then runs `npx hyperframes render`.

The renderer prepares local `ffmpeg`/`ffprobe` binaries under `data/tools/` when needed so HyperFrames can encode without a separate system install.

The HyperFrames version renders crisp PDF page images and zooms into the PDF-derived heading or main highlighted line for each section. Marker highlights sweep over the exact target phrase, then fade out. The rendered video does not include a logo, agency branding, or a presenter badge.

The generated video is client-facing: the script speaks directly to the viewer of the report, not as an internal sales-team note. The upload form supports two output formats:

- `Horizontal 16:9` for desktop walkthroughs and client review calls.
- `Vertical 9:16` for mobile-first sharing, reels, and shorts.

Set `VIDEO_RENDERER=local` if you need the older PIL/FFmpeg renderer.

## Outputs

Each run creates a folder under `data/jobs/<job-id>/` containing:

- `input.pdf`
- rendered PDF pages
- `voiceover-script.txt`
- `script.json`
- `voiceover.wav` or `voiceover.mp3`
- `walkthrough.mp4`
- `hyperframes-render/` when `VIDEO_RENDERER=hyperframes`

## Presenter support

Presenter/avatar controls are disabled in the UI. Generated videos start directly with the PDF explanation.
