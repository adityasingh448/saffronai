from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
import json
import os
import platform
import shutil
import subprocess
import sys

from imageio_ffmpeg import get_ffmpeg_exe
from PIL import Image

from app.audio import audio_duration_seconds
from app.config import settings
from app.pdf_tools import HighlightBox
from app.script_writer import PageScript


@dataclass(frozen=True)
class VideoLayout:
    key: str
    width: int
    height: int
    page_display_width: int
    stage_width: int
    stage_height: int
    scene_pad_top: int
    scene_pad_x: int
    scene_pad_bottom: int
    header_height: int
    scene_gap: int
    page_title_max_width: int
    page_title_font: int
    note_width: int
    note_right: int
    note_bottom: int
    note_font: int
    focus_scale: float


def can_use_hyperframes() -> bool:
    return settings.hyperframes_template_dir.exists() and bool(_resolve_hyperframes_cli())


def compose_hyperframes_walkthrough_video(
    page_images: list[Path],
    page_scripts: list[PageScript],
    page_highlights: list[list[HighlightBox]] | None,
    audio_path: Path,
    output_path: Path,
    title: str,
    brand_name: str,
    avatar_mode: str,
    prospect_label: str,
    video_format: str = "horizontal",
) -> Path:
    if not page_images:
        raise ValueError("No PDF page images were provided for HyperFrames rendering.")

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    project_dir = output_path.parent / "hyperframes-render"
    _prepare_project(
        project_dir,
        page_images,
        page_scripts,
        page_highlights,
        audio_path,
        title,
        brand_name,
        avatar_mode,
        prospect_label,
        video_format,
    )
    _render_project(project_dir, output_path)

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError("HyperFrames render finished without producing a video file.")

    return output_path


def _prepare_project(
    project_dir: Path,
    page_images: list[Path],
    page_scripts: list[PageScript],
    page_highlights: list[list[HighlightBox]] | None,
    audio_path: Path,
    title: str,
    brand_name: str,
    avatar_mode: str,
    prospect_label: str,
    video_format: str,
) -> None:
    if project_dir.exists():
        shutil.rmtree(project_dir)

    assets_dir = project_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    template_dir = settings.hyperframes_template_dir
    for filename in ("package.json", "hyperframes.json", "meta.json", "AGENTS.md", "CLAUDE.md", "DESIGN.md"):
        source = template_dir / filename
        if source.exists():
            shutil.copy2(source, project_dir / filename)

    copied_pages = []
    for index, source in enumerate(page_images, start=1):
        target_name = f"page-{index:02d}{source.suffix or '.png'}"
        shutil.copy2(source, assets_dir / target_name)
        copied_pages.append(target_name)

    audio_suffix = audio_path.suffix or ".wav"
    audio_name = f"voiceover{audio_suffix}"
    shutil.copy2(audio_path, assets_dir / audio_name)

    duration = max(audio_duration_seconds(audio_path), len(page_images) * 4.0)
    page_durations = _page_durations(page_scripts, duration, len(page_images))
    layout = _video_layout(video_format)

    html = _build_index_html(
        layout=layout,
        page_images=page_images,
        copied_pages=copied_pages,
        page_scripts=page_scripts,
        page_highlights=page_highlights or [[] for _ in page_images],
        audio_name=audio_name,
        duration=duration,
        page_durations=page_durations,
        title=title,
        brand_name=brand_name,
        avatar_mode=avatar_mode,
        prospect_label=prospect_label,
    )
    (project_dir / "index.html").write_text(html, encoding="utf-8")


def _render_project(project_dir: Path, output_path: Path) -> None:
    cli = _resolve_hyperframes_cli()
    if not cli:
        raise RuntimeError("HyperFrames CLI was not found. Install Node.js/npm, or set HYPERFRAMES_CLI.")

    env = _hyperframes_subprocess_env()
    command = [
        cli,
        "--yes",
        settings.hyperframes_package,
        "render",
        "--fps",
        str(settings.hyperframes_fps),
        "--quality",
        settings.hyperframes_quality,
        "--strict",
        "--output",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        cwd=project_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=settings.hyperframes_render_timeout_seconds,
    )
    if completed.returncode != 0:
        detail = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        if len(detail) > 4000:
            detail = detail[-4000:]
        raise RuntimeError(f"HyperFrames render failed with exit code {completed.returncode}.\n{detail}")


def _resolve_hyperframes_cli() -> str | None:
    configured = settings.hyperframes_cli.strip()
    if configured:
        return configured

    if os.name == "nt":
        return shutil.which("npx.cmd") or shutil.which("npx")

    return shutil.which("npx")


def _hyperframes_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    media_bin = _ensure_media_binaries()
    env["PATH"] = str(media_bin) + os.pathsep + env.get("PATH", "")
    return env


def _ensure_media_binaries() -> Path:
    bin_dir = settings.hyperframes_media_tools_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    ffmpeg_target = bin_dir / ffmpeg_name
    if not ffmpeg_target.exists():
        ffmpeg_source = Path(get_ffmpeg_exe())
        shutil.copy2(ffmpeg_source, ffmpeg_target)
        _make_executable(ffmpeg_target)

    ffprobe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    ffprobe_target = bin_dir / ffprobe_name
    if not ffprobe_target.exists():
        ffprobe_source = _find_cached_ffprobe()
        if not ffprobe_source:
            _install_ffprobe_package()
            ffprobe_source = _find_cached_ffprobe()
        if not ffprobe_source:
            raise RuntimeError("Unable to prepare ffprobe for HyperFrames rendering.")
        shutil.copy2(ffprobe_source, ffprobe_target)
        _make_executable(ffprobe_target)

    return bin_dir


def _find_cached_ffprobe() -> Path | None:
    tools_dir = settings.hyperframes_media_tools_dir
    filename = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    candidates = [path for path in tools_dir.rglob(filename) if path.is_file()]
    if not candidates:
        return None

    platform_family, platform_arch = _ffprobe_platform_key()
    preferred = [
        path
        for path in candidates
        if platform_family in {part.lower() for part in path.parts}
        and platform_arch in {part.lower() for part in path.parts}
    ]
    return (preferred or candidates)[0]


def _ffprobe_platform_key() -> tuple[str, str]:
    machine = platform.machine().lower()
    is_x64 = machine in {"amd64", "x86_64", "x64"}
    is_arm64 = machine in {"arm64", "aarch64"}

    if os.name == "nt":
        return "win32", "x64" if is_x64 else "ia32"
    if sys.platform == "darwin":
        return "darwin", "arm64" if is_arm64 else "x64"
    return "linux", "x64" if is_x64 else "ia32"


def _install_ffprobe_package() -> None:
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")
    if not npm:
        raise RuntimeError("npm was not found, so ffprobe-static cannot be installed for HyperFrames.")

    completed = subprocess.run(
        [
            npm,
            "install",
            "--prefix",
            str(settings.hyperframes_media_tools_dir),
            settings.hyperframes_ffprobe_package,
            "--no-save",
        ],
        text=True,
        capture_output=True,
        timeout=600,
    )
    if completed.returncode != 0:
        detail = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        raise RuntimeError(f"Unable to install {settings.hyperframes_ffprobe_package}.\n{detail}")


def _make_executable(path: Path) -> None:
    if os.name != "nt":
        path.chmod(path.stat().st_mode | 0o755)


def _build_index_html(
    layout: VideoLayout,
    page_images: list[Path],
    copied_pages: list[str],
    page_scripts: list[PageScript],
    page_highlights: list[list[HighlightBox]],
    audio_name: str,
    duration: float,
    page_durations: list[float],
    title: str,
    brand_name: str,
    avatar_mode: str,
    prospect_label: str,
) -> str:
    starts: list[float] = []
    cursor = 0.0
    for item_duration in page_durations:
        starts.append(round(cursor, 3))
        cursor += item_duration

    scenes = []
    camera_moves = []
    focus_targets = []
    for index, image_path in enumerate(page_images):
        script = page_scripts[min(index, len(page_scripts) - 1)] if page_scripts else None
        highlights = page_highlights[index] if index < len(page_highlights) else []
        highlights = _relabeled_highlights(highlights, script.highlight_terms if script else [])
        focus = script.focus if script else f"Page {index + 1}"
        targets = _focus_targets(image_path, highlights, layout, index + 1)
        scenes.append(
            {
                "id": f"scene-{index + 1}",
                "asset": copied_pages[index],
                "start": starts[index],
                "duration": round(page_durations[index], 3),
                "track": 1 + (index % 2),
                "kicker": _scene_kicker(index, len(page_images)),
                "title": _trim(focus, 58),
                "note": _insight_note(focus, highlights),
                "count": f"{index + 1:02d} / {len(page_images):02d}",
            }
        )
        camera_moves.append(_camera_moves(image_path, targets, layout, page_durations[index]))
        focus_targets.append(targets)

    transition_times = [round(max(0.1, start - 0.46), 3) for start in starts[1:]]
    closing_start = round(max(0.6, duration - min(5.6, max(2.2, duration * 0.16))), 3)
    closing_end = round(max(closing_start + 0.8, duration - 0.55), 3)
    closing_note_fade = round(max(0.35, closing_start - 0.38), 3)
    ambient_repeats = max(1, int(duration / 5.6))

    scene_html = "\n\n".join(_scene_html(scene, focus_targets[index]) for index, scene in enumerate(scenes))
    format_class = f"format-{layout.key}"

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={layout.width}, height={layout.height}" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * {{
        margin: 0;
        padding: 0;
        box-sizing: border-box;
      }}

      html,
      body {{
        margin: 0;
        width: {layout.width}px;
        height: {layout.height}px;
        overflow: hidden;
        background: #f4f7fb;
      }}

      body {{
        font-family: "Segoe UI", Arial, sans-serif;
        color: #182232;
      }}

      #root {{
        position: relative;
        width: {layout.width}px;
        height: {layout.height}px;
        overflow: hidden;
      }}

      .base-fill {{
        position: absolute;
        inset: 0;
        z-index: 0;
        background: #f8fbff;
      }}

      .abstract-field {{
        position: absolute;
        inset: -18%;
        z-index: 1;
        pointer-events: none;
        overflow: hidden;
        background:
          linear-gradient(120deg, rgba(255, 241, 232, 0.96) 0%, rgba(255, 241, 232, 0.32) 32%, rgba(236, 248, 255, 0.72) 68%, rgba(236, 248, 255, 0.96) 100%),
          linear-gradient(36deg, rgba(236, 248, 255, 0.88) 0%, rgba(255, 241, 232, 0.78) 100%);
        transform: rotate(-4deg) scale(1.08);
      }}

      .color-sheet {{
        position: absolute;
        inset: -10%;
        background:
          linear-gradient(88deg, rgba(255, 241, 232, 0.84), rgba(236, 248, 255, 0.26) 46%, rgba(236, 248, 255, 0.78)),
          linear-gradient(152deg, rgba(236, 248, 255, 0.82), rgba(255, 241, 232, 0.62));
        opacity: 0.74;
      }}

      .page-scene {{
        position: absolute;
        inset: 0;
        z-index: 10;
      }}

      .scene-content {{
        position: relative;
        width: 100%;
        height: 100%;
        padding: {layout.scene_pad_top}px {layout.scene_pad_x}px {layout.scene_pad_bottom}px;
        display: flex;
        flex-direction: column;
        gap: {layout.scene_gap}px;
      }}

      .scene-header {{
        display: flex;
        align-items: end;
        justify-content: space-between;
        min-height: {layout.header_height}px;
        gap: 24px;
      }}

      .page-kicker {{
        font-size: 18px;
        color: #ee6723;
        font-weight: 900;
        text-transform: uppercase;
      }}

      .page-title {{
        margin-top: 4px;
        max-width: {layout.page_title_max_width}px;
        font-size: {layout.page_title_font}px;
        line-height: 1.04;
        font-weight: 900;
      }}

      .page-count {{
        font-family: Consolas, monospace;
        font-variant-numeric: tabular-nums;
        font-size: 20px;
        color: #66758a;
        flex: 0 0 auto;
      }}

      .report-stage {{
        position: relative;
        flex: 0 0 {layout.stage_height}px;
        width: {layout.stage_width}px;
        height: {layout.stage_height}px;
        border: 1px solid #d9e2ee;
        border-radius: 16px;
        background: #fffaf6;
        overflow: hidden;
        box-shadow: 0 24px 68px rgba(24, 34, 50, 0.12);
      }}

      .page-camera {{
        position: absolute;
        inset: 0;
        transform-origin: center center;
        will-change: transform;
      }}

      .page-image {{
        position: absolute;
        left: 50%;
        top: 22px;
        width: {layout.page_display_width}px;
        height: auto;
        transform: translateX(-50%);
        background: #ffffff;
        box-shadow: 0 18px 28px rgba(24, 34, 50, 0.10);
        image-rendering: auto;
        backface-visibility: hidden;
      }}

      .soft-mask {{
        position: absolute;
        left: 0;
        right: 0;
        z-index: 12;
        pointer-events: none;
      }}

      .soft-mask.top {{
        top: 0;
        height: 110px;
        background: linear-gradient(#fffaf6, rgba(255, 250, 246, 0));
      }}

      .soft-mask.bottom {{
        bottom: 0;
        height: 110px;
        background: linear-gradient(rgba(255, 250, 246, 0), #fffaf6);
      }}

      .marker-line {{
        position: absolute;
        z-index: 14;
        min-height: 16px;
        border-radius: 7px 10px 6px 9px;
        background: rgba(255, 208, 74, 0.48);
        box-shadow: 0 6px 18px rgba(238, 103, 35, 0.14);
        mix-blend-mode: multiply;
        opacity: 0;
        transform: scaleX(0) rotate(-0.35deg);
        transform-origin: left center;
      }}

      .marker-line::after {{
        content: "";
        position: absolute;
        inset: -2px 0;
        border-radius: inherit;
        background: linear-gradient(90deg, rgba(255, 246, 177, 0.18), rgba(238, 103, 35, 0.17), rgba(255, 246, 177, 0.10));
        opacity: 0.75;
      }}

      .insight-note {{
        position: absolute;
        right: {layout.note_right}px;
        bottom: {layout.note_bottom}px;
        z-index: 18;
        width: {layout.note_width}px;
        padding: 22px 24px;
        border: 1px solid #ffd1bb;
        border-radius: 14px;
        background: rgba(255, 247, 241, 0.94);
        box-shadow: 0 18px 42px rgba(24, 34, 50, 0.10);
      }}

      .insight-note strong {{
        display: block;
        margin-bottom: 8px;
        color: #ee6723;
        font-size: 17px;
        text-transform: uppercase;
      }}

      .insight-note span {{
        display: block;
        font-family: Georgia, serif;
        color: #182232;
        font-size: {layout.note_font}px;
        line-height: 1.24;
      }}

      .transition-veil {{
        position: absolute;
        inset: 0;
        z-index: 70;
        pointer-events: none;
      }}

      .veil-panel {{
        position: absolute;
        top: 0;
        bottom: 0;
        width: 25%;
        background: #fff1e8;
        opacity: 0;
        transform-origin: left center;
      }}

      .veil-panel:nth-child(1) {{ left: 0; }}
      .veil-panel:nth-child(2) {{ left: 25%; background: #ecf8ff; }}
      .veil-panel:nth-child(3) {{ left: 50%; }}
      .veil-panel:nth-child(4) {{ left: 75%; background: #ecf8ff; }}

      .closing-card {{
        position: absolute;
        z-index: 55;
        left: 50%;
        bottom: 92px;
        width: 680px;
        transform: translateX(-50%);
        padding: 30px 36px;
        border: 1px solid #ffd1bb;
        border-radius: 16px;
        background: rgba(255, 247, 241, 0.96);
        text-align: center;
        box-shadow: 0 22px 54px rgba(24, 34, 50, 0.14);
        opacity: 0;
      }}

      .closing-card h2 {{
        font-size: 46px;
        line-height: 1.04;
        color: #182232;
      }}

      .closing-card p {{
        margin-top: 12px;
        font-family: Georgia, serif;
        font-size: 24px;
        color: #66758a;
      }}
    </style>
  </head>
  <body>
    <div
      id="root"
      class="{format_class}"
      data-composition-id="main"
      data-start="0"
      data-duration="{duration:.3f}"
      data-width="{layout.width}"
      data-height="{layout.height}"
    >
      <div class="base-fill" data-layout-ignore></div>
      <div class="abstract-field" data-layout-ignore>
        <div class="color-sheet"></div>
      </div>

      <audio
        id="voiceover"
        data-start="0"
        data-duration="{duration:.3f}"
        data-track-index="20"
        src="assets/{escape(audio_name)}"
        data-volume="1"
      ></audio>

{scene_html}

      <div id="closing-card" class="closing-card">
        <h2>Your focused action plan starts here.</h2>
        <p>Prioritize the quickest fixes, then measure progress weekly.</p>
      </div>

      <div id="transition-veil" class="transition-veil">
        <div class="veil-panel"></div>
        <div class="veil-panel"></div>
        <div class="veil-panel"></div>
        <div class="veil-panel"></div>
      </div>
    </div>

    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});

      const sceneStarts = {json.dumps(starts)};
      const sceneDurations = {json.dumps([round(item, 3) for item in page_durations])};
      const cameraMoves = {json.dumps(camera_moves)};

      sceneStarts.forEach((start, index) => {{
        const id = `#scene-${{index + 1}}`;
        tl.from(`${{id}} .page-kicker`, {{ opacity: 0, x: -42, duration: 0.42, ease: "power3.out" }}, start + 0.18);
        tl.from(`${{id}} .page-title`, {{ opacity: 0, y: 34, duration: 0.56, ease: "expo.out" }}, start + 0.28);
        tl.from(`${{id}} .page-count`, {{ opacity: 0, y: -18, duration: 0.38, ease: "back.out(1.5)" }}, start + 0.38);
        tl.from(`${{id}} .report-stage`, {{ opacity: 0, y: 44, scale: 0.985, duration: 0.64, ease: "power2.out" }}, start + 0.42);
        tl.from(`${{id}} .page-camera`, {{ opacity: 0, y: 28, scale: 0.985, duration: 0.78, ease: "sine.out" }}, start + 0.58);
        tl.from(`${{id}} .insight-note`, {{ opacity: 0, x: 32, duration: 0.52, ease: "power1.out" }}, start + 1.1);

        (cameraMoves[index] || []).forEach((move) => {{
          const cue = start + move.at;
          tl.to(`${{id}} .page-camera`, {{
            x: move.x,
            y: move.y,
            scale: move.scale,
            duration: move.duration,
            ease: move.ease || "sine.inOut"
          }}, cue);

          if (move.markerId) {{
            tl.fromTo(`#${{move.markerId}}`, {{
              opacity: 0,
              scaleX: 0,
              rotation: -0.35,
              transformOrigin: "left center"
            }}, {{
              opacity: 1,
              scaleX: 1,
              duration: 0.58,
              ease: "power2.out"
            }}, cue + 0.34);
            tl.to(`#${{move.markerId}}`, {{
              opacity: 0,
              duration: 0.64,
              ease: "sine.inOut"
            }}, cue + Math.min(2.6, Math.max(1.55, move.duration + 0.62)));
          }}
        }});
      }});

      {json.dumps(transition_times)}.forEach((time) => {{
        tl.fromTo("#transition-veil .veil-panel", {{ scaleX: 0, opacity: 1 }}, {{ scaleX: 1, duration: 0.32, stagger: 0.035, ease: "power3.inOut" }}, time);
        tl.to("#transition-veil .veil-panel", {{ scaleX: 0, transformOrigin: "right center", duration: 0.32, stagger: 0.035, ease: "power3.inOut" }}, time + 0.36);
        tl.set("#transition-veil .veil-panel", {{ opacity: 0, transformOrigin: "left center" }}, time + 0.74);
      }});

      tl.from(".abstract-field", {{ opacity: 0, scale: 1.02, duration: 0.9, ease: "sine.out" }}, 0.12);
      tl.to(".abstract-field", {{ x: 46, y: -28, rotation: 2, duration: 5.6, repeat: {ambient_repeats}, yoyo: true, ease: "sine.inOut" }}, 0.6);
      tl.to(".color-sheet", {{ x: -62, y: 38, scale: 1.08, duration: 6.4, repeat: {ambient_repeats}, yoyo: true, ease: "sine.inOut" }}, 0.9);
      tl.to(".insight-note", {{ opacity: 0, y: 18, duration: 0.34, ease: "sine.inOut" }}, {closing_note_fade});
      tl.fromTo("#closing-card", {{ opacity: 0, y: 42, scale: 0.96 }}, {{ opacity: 1, y: 0, scale: 1, duration: 0.72, ease: "power2.out" }}, {closing_start});
      tl.to("#closing-card", {{ opacity: 0, y: -18, duration: 0.55, ease: "sine.inOut" }}, {closing_end});

      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""


def _scene_html(scene: dict[str, object], targets: list[dict[str, object]]) -> str:
    lines = ""
    return f"""      <div id="{scene["id"]}" class="clip page-scene" data-start="{scene["start"]:.3f}" data-duration="{scene["duration"]:.3f}" data-track-index="{scene["track"]}">
        <div class="scene-content">
          <div class="scene-header">
            <div>
              <div class="page-kicker">{escape(str(scene["kicker"]))}</div>
              <div class="page-title">{escape(str(scene["title"]))}</div>
            </div>
            <div class="page-count">{escape(str(scene["count"]))}</div>
          </div>
          <div class="report-stage" data-layout-allow-overflow>
            <div class="page-camera">
              <img class="page-image" src="assets/{escape(str(scene["asset"]))}" />
{lines}
            </div>
            <div class="soft-mask top"></div>
            <div class="soft-mask bottom"></div>
          </div>
          <div class="insight-note"><strong>What this means</strong><span>{escape(str(scene["note"]))}</span></div>
        </div>
      </div>"""


def _page_durations(page_scripts: list[PageScript], total_duration: float, page_count: int) -> list[float]:
    if page_count <= 0:
        return []
    if not page_scripts:
        return [total_duration / page_count] * page_count

    weights = []
    for index in range(page_count):
        script = page_scripts[min(index, len(page_scripts) - 1)]
        weights.append(max(len(script.narration.split()), 45))

    total_weight = sum(weights) or page_count
    durations = [max(3.0, total_duration * weight / total_weight) for weight in weights]
    scale = total_duration / sum(durations)
    return [duration * scale for duration in durations]


def _scene_kicker(index: int, total: int) -> str:
    if index == 0:
        return "Opening diagnosis"
    if index == total - 1:
        return "Recommended next step"
    return f"Page {index + 1:02d} insight"


def _insight_note(focus: str, highlights: list[HighlightBox]) -> str:
    if highlights:
        label = _trim(highlights[0].label, 74)
        return f"This point around {label} shows where your team can focus next."
    return _trim(focus, 130) or "This report section helps your team choose the next practical action."


def _relabeled_highlights(highlights: list[HighlightBox], labels: list[str]) -> list[HighlightBox]:
    clean_labels = [_trim(label, 90) for label in labels if _trim(label, 90)]
    if not clean_labels:
        return highlights
    return [
        HighlightBox(
            x0=item.x0,
            y0=item.y0,
            x1=item.x1,
            y1=item.y1,
            label=clean_labels[index] if index < len(clean_labels) else item.label,
        )
        for index, item in enumerate(highlights)
    ]


def _focus_targets(
    image_path: Path,
    highlights: list[HighlightBox],
    layout: VideoLayout,
    scene_number: int,
) -> list[dict[str, object]]:
    try:
        with Image.open(image_path) as image:
            scale = layout.page_display_width / max(1, image.width)
    except Exception:
        scale = 1.0

    page_left = (layout.stage_width - layout.page_display_width) / 2
    page_top = 22
    targets: list[dict[str, object]] = []

    for index, box in enumerate(highlights[:5], start=1):
        x = page_left + (box.x0 * scale)
        y = page_top + (box.y0 * scale)
        width = max(32, (box.x1 - box.x0) * scale)
        height = max(16, (box.y1 - box.y0) * scale)
        targets.append(
            {
                "id": f"marker-{scene_number}-{index}",
                "x": round(x - 5),
                "y": round(y - 3),
                "width": round(width + 10),
                "height": round(height + 6),
                "cx": round(x + width / 2, 2),
                "cy": round(y + height / 2, 2),
            }
        )
        if len(targets) == 5:
            break

    return targets


def _camera_moves(
    image_path: Path,
    targets: list[dict[str, object]],
    layout: VideoLayout,
    scene_duration: float,
) -> list[dict[str, object]]:
    scaled_height = _scaled_page_height(image_path, layout)
    moves: list[dict[str, object]] = []

    if not targets:
        return [
            {
                "at": 1.35,
                "duration": max(2.6, min(6.0, scene_duration - 2.2)),
                "x": 0,
                "y": _pan_distance(scaled_height, layout),
                "scale": 1.025,
                "ease": "sine.inOut",
            }
        ]

    entry_at = min(1.75, max(0.95, scene_duration * 0.28))
    usable = max(1.4, scene_duration - entry_at - 1.1)
    spacing = usable / max(len(targets), 1)
    for index, target in enumerate(targets):
        scale = layout.focus_scale + (0.018 if index % 2 else 0)
        x, y = _camera_xy_for_target(float(target["cx"]), float(target["cy"]), scaled_height, scale, layout)
        moves.append(
            {
                "at": round(entry_at + spacing * index, 3),
                "duration": round(max(1.05, min(2.8, spacing * 0.72)), 3),
                "x": round(x, 2),
                "y": round(y, 2),
                "scale": round(scale, 3),
                "ease": "sine.inOut",
            }
        )

    moves.append(
        {
            "at": round(max(1.5, scene_duration - 1.65), 3),
            "duration": 1.35,
            "x": 0,
            "y": _pan_distance(scaled_height, layout),
            "scale": 1.012,
            "ease": "power1.inOut",
        }
    )
    return moves


def _scaled_page_height(image_path: Path, layout: VideoLayout) -> float:
    try:
        with Image.open(image_path) as image:
            return image.height * (layout.page_display_width / max(1, image.width))
    except Exception:
        return layout.stage_height * 1.5


def _camera_xy_for_target(
    focus_x: float,
    focus_y: float,
    scaled_height: float,
    scale: float,
    layout: VideoLayout,
) -> tuple[float, float]:
    desired_x = (layout.stage_width * 0.5) - (focus_x * scale)
    desired_y = (layout.stage_height * 0.46) - (focus_y * scale)

    page_left = (layout.stage_width - layout.page_display_width) / 2
    page_top = 22
    min_x = min(0.0, layout.stage_width - ((page_left + layout.page_display_width) * scale) - 36)
    max_x = max(0.0, 36 - (page_left * scale))
    min_y = min(0.0, layout.stage_height - ((page_top + scaled_height) * scale) - 42)
    max_y = max(0.0, 56 - (page_top * scale))
    return _clamp(desired_x, min_x, max_x), _clamp(desired_y, min_y, max_y)


def _pan_distance(scaled_height: float, layout: VideoLayout) -> int:
    if scaled_height <= layout.stage_height - 60:
        return -24

    return -int(max(120, min(layout.stage_height * 0.58, scaled_height - (layout.stage_height - 90))))


def _video_layout(video_format: str) -> VideoLayout:
    normalized = (video_format or "horizontal").strip().lower()
    if normalized in {"vertical", "portrait", "9:16", "reels", "shorts"}:
        return VideoLayout(
            key="vertical",
            width=1080,
            height=1920,
            page_display_width=860,
            stage_width=996,
            stage_height=1606,
            scene_pad_top=112,
            scene_pad_x=42,
            scene_pad_bottom=78,
            header_height=98,
            scene_gap=26,
            page_title_max_width=760,
            page_title_font=44,
            note_width=460,
            note_right=56,
            note_bottom=92,
            note_font=24,
            focus_scale=1.17,
        )

    return VideoLayout(
        key="horizontal",
        width=1920,
        height=1080,
        page_display_width=1050,
        stage_width=1776,
        stage_height=838,
        scene_pad_top=86,
        scene_pad_x=72,
        scene_pad_bottom=76,
        header_height=58,
        scene_gap=22,
        page_title_max_width=1420,
        page_title_font=45,
        note_width=430,
        note_right=116,
        note_bottom=114,
        note_font=23,
        focus_scale=1.13,
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _trim(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
