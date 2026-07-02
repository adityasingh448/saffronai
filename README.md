# Saffron AI Sales Automation Agent

This MVP turns a prospect report PDF into a personalized walkthrough video:

1. Upload a PDF report.
2. Extract page text and render each page as an image.
3. Generate a consultative voiceover script.
4. Create natural voiceover audio with ElevenLabs v3 when a key is configured.
5. Render a Remotion MP4 with smooth camera zooms, report highlights, side motion graphics, and no presenter/avatar branding.

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

Required for realistic report walkthrough voiceover:

```env
ELEVENLABS_API_KEY=your_key
ELEVENLABS_VOICE_ID=your_custom_voice_id
ELEVENLABS_MODEL_ID=eleven_v3
```

Without keys, the app still works in demo mode:

- Script generation uses a local deterministic writer.
- Voiceover uses Windows local speech when available. This is only a demo fallback; production-quality natural emotion requires ElevenLabs or another production TTS provider.

The upload form fetches saved/custom voices from the configured ElevenLabs account and shows them as selectable voice cards with preview buttons. The selected voice ID is stored on the job and used for the final video narration.

## Video rendering

Remotion is linked into the main agent pipeline by default:

```env
VIDEO_RENDERER=remotion
REMOTION_TEMPLATE_DIR=remotion-report
REMOTION_FPS=60
REMOTION_CRF=18
REMOTION_CODEC=h264
```

Each job copies the rendered PDF pages, voiceover, and a generated `props.json` file into `remotion-report/public/jobs/<job-id>/`, then runs Remotion from the `remotion-report` project.

On the first Remotion job, the Python backend runs `npm install` inside `remotion-report` if dependencies are missing. Remotion v4 bundles FFmpeg support, so a separate system FFmpeg install is not required for normal renders.

The Remotion version renders crisp 1080p/60fps PDF page images, zooms into the PDF-derived heading or main highlighted line for each section, and highlights the exact target phrase while a side motion panel explains the current pointer. The rendered video does not include a logo, agency branding, or a presenter badge.

The generated video is viewer-facing: the script speaks directly to the person watching the report, not as an internal sales-team note. The upload form supports two output formats:

- `Horizontal 16:9` for desktop walkthroughs and review calls.
- `Vertical 9:16` for mobile-first sharing, reels, and shorts.

Set `VIDEO_RENDERER=local` if you need the older PIL/FFmpeg renderer, or `VIDEO_RENDERER=hyperframes` only if you want to use the legacy HyperFrames renderer.

## Outputs

Each run creates a folder under `data/jobs/<job-id>/` containing:

- `input.pdf`
- rendered PDF pages
- `voiceover-script.txt`
- `script.json`
- `voiceover.wav` or `voiceover.mp3`
- `walkthrough.mp4`
- generated Remotion job assets under `remotion-report/public/jobs/<job-id>/` when `VIDEO_RENDERER=remotion`

## Presenter support

Presenter/avatar controls are disabled in the UI. Generated videos start directly with the PDF explanation.
