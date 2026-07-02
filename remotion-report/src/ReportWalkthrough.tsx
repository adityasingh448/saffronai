import React from 'react';
import {Audio} from '@remotion/media';
import {
  AbsoluteFill,
  Easing,
  Img,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

export type HighlightBox = {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  label: string;
};

export type PageData = {
  pageNumber: number;
  imageSrc: string;
  width: number;
  height: number;
  startSeconds: number;
  durationSeconds: number;
  focus: string;
  narration: string;
  highlights: HighlightBox[];
};

export type ReportWalkthroughProps = {
  title: string;
  brandName: string;
  avatarMode: string;
  prospectLabel: string;
  videoFormat: 'horizontal' | 'vertical';
  fps: number;
  durationSeconds: number;
  audioSrc: string;
  pages: PageData[];
};

type Rect = {
  left: number;
  top: number;
  width: number;
  height: number;
};

type Layout = {
  page: Rect;
  panel: Rect;
  titleTop: number;
  titleLeft: number;
  titleWidth: number;
  titleSize: number;
  pointStrip: Rect;
};

type CameraState = {
  x: number;
  y: number;
  scale: number;
};

const SAFFRON = '#ee6723';
const GOLD = '#f3ae32';
const INK = '#171f2f';
const MUTED = '#66758a';
const LINE = '#d8e2ef';

export const ReportWalkthrough: React.FC<ReportWalkthroughProps> = (props) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const layout = getLayout(width, height);
  const bgDrift = Math.sin(frame / (fps * 3.8));

  return (
    <AbsoluteFill
      style={{
        backgroundColor: '#f5f8fc',
        fontFamily: 'Inter, Segoe UI, Arial, sans-serif',
        overflow: 'hidden',
        color: INK,
      }}
    >
      <div
        style={{
          position: 'absolute',
          inset: 0,
          backgroundImage:
            'radial-gradient(circle, rgba(133, 149, 169, 0.34) 1.35px, transparent 1.35px)',
          backgroundSize: '30px 30px',
        }}
      />
      <div
        style={{
          position: 'absolute',
          inset: '-14%',
          background:
            'linear-gradient(120deg, rgba(255,244,237,0.88), rgba(236,248,255,0.62) 48%, rgba(255,249,232,0.76))',
          transform: `translate(${bgDrift * 24}px, ${bgDrift * -18}px) rotate(-3deg)`,
          opacity: 0.84,
        }}
      />

      {props.audioSrc ? <Audio src={staticFile(props.audioSrc)} /> : null}

      <div
        style={{
          position: 'absolute',
          left: layout.titleLeft,
          top: layout.titleTop,
          width: layout.titleWidth,
          zIndex: 20,
        }}
      >
        <div
          style={{
            color: SAFFRON,
            fontSize: 18,
            fontWeight: 900,
            textTransform: 'uppercase',
          }}
        >
          Report walkthrough
        </div>
        <div
          style={{
            marginTop: 8,
            fontSize: layout.titleSize,
            lineHeight: 1.03,
            fontWeight: 900,
            letterSpacing: 0,
          }}
        >
          {trim(props.title || props.prospectLabel || 'Report walkthrough', 84)}
        </div>
      </div>

      {props.pages.length === 0 ? <EmptyState /> : null}
      {props.pages.map((page, index) => {
        const from = Math.max(0, Math.round(page.startSeconds * fps));
        const duration = Math.max(1, Math.round(page.durationSeconds * fps));
        return (
          <Sequence from={from} durationInFrames={duration + 2} key={page.pageNumber}>
            <PageScene
              page={page}
              index={index}
              total={props.pages.length}
              layout={layout}
              durationFrames={duration}
            />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

const PageScene: React.FC<{
  page: PageData;
  index: number;
  total: number;
  layout: Layout;
  durationFrames: number;
}> = ({page, index, total, layout, durationFrames}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const pointers = normalizedHighlights(page);
  const pointerCount = Math.max(1, pointers.length);
  const sceneProgress = clamp(frame / Math.max(1, durationFrames), 0, 0.999);
  const pointerFloat = sceneProgress * pointerCount;
  const pointerIndex = Math.min(pointerCount - 1, Math.floor(pointerFloat));
  const pointerProgress = clamp(pointerFloat - pointerIndex, 0, 1);
  const activePointer = pointers[pointerIndex];
  const previousPointer = pointerIndex > 0 ? pointers[pointerIndex - 1] : undefined;

  const enter = interpolate(frame, [0, fps * 0.62], [0, 1], {
    easing: Easing.bezier(0.16, 1, 0.3, 1),
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const exit = interpolate(
    frame,
    [durationFrames - fps * 0.42, durationFrames],
    [0, 1],
    {
      easing: Easing.in(Easing.cubic),
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    },
  );
  const sceneOpacity = clamp(enter - exit, 0, 1);
  const camera = cameraForPointer(
    page,
    layout.page,
    activePointer,
    previousPointer,
    pointerProgress,
    frame,
  );
  const markerOpacity = highlightOpacity(pointerProgress);

  return (
    <AbsoluteFill style={{opacity: sceneOpacity}}>
      <div
        style={{
          position: 'absolute',
          left: layout.page.left,
          top: layout.page.top,
          width: layout.page.width,
          height: layout.page.height,
          borderRadius: 14,
          overflow: 'hidden',
          background: '#fffaf6',
          border: `2px solid ${LINE}`,
          boxShadow: '0 28px 76px rgba(24, 34, 50, 0.16)',
          transform: `translateY(${(1 - enter) * 34}px) scale(${0.992 + enter * 0.008})`,
        }}
      >
        <div
          style={{
            position: 'absolute',
            width: page.width,
            height: page.height,
            transformOrigin: 'top left',
            transform: `translate(${camera.x}px, ${camera.y}px) scale(${camera.scale})`,
          }}
        >
          <Img
            src={staticFile(page.imageSrc)}
            style={{
              position: 'absolute',
              left: 0,
              top: 0,
              width: page.width,
              height: page.height,
              boxShadow: '0 20px 42px rgba(24, 34, 50, 0.12)',
            }}
          />
          {pointers.map((pointer, markerIndex) => {
            const active = markerIndex === pointerIndex;
            return (
              <div
                key={`${pointer.label}-${markerIndex}`}
                style={{
                  position: 'absolute',
                  left: pointer.x0 - 8,
                  top: pointer.y0 - 5,
                  width: Math.max(22, pointer.x1 - pointer.x0 + 16),
                  height: Math.max(16, pointer.y1 - pointer.y0 + 10),
                  borderRadius: 8,
                  background: 'rgba(255, 215, 72, 0.46)',
                  border: `3px solid ${SAFFRON}`,
                  boxShadow: '0 12px 28px rgba(238, 103, 35, 0.18)',
                  opacity: active ? markerOpacity : 0,
                  transform: `scaleX(${active ? Math.max(0.08, markerOpacity) : 0})`,
                  transformOrigin: 'left center',
                  mixBlendMode: 'multiply',
                }}
              />
            );
          })}
        </div>
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background:
              'linear-gradient(180deg, rgba(255,250,246,0.98), transparent 14%, transparent 84%, rgba(255,250,246,0.98))',
            pointerEvents: 'none',
          }}
        />
      </div>

      <SidePanel
        rect={layout.panel}
        pointer={activePointer}
        pointerIndex={pointerIndex}
        pointerCount={pointerCount}
        focus={page.focus}
        enter={enter}
        frame={frame}
      />

      <PointerStrip
        rect={layout.pointStrip}
        pageNumber={index + 1}
        total={total}
        pointer={activePointer}
        pointerIndex={pointerIndex}
        pointerCount={pointerCount}
      />

      <WhiteFlash frame={frame} durationFrames={durationFrames} />
    </AbsoluteFill>
  );
};

const SidePanel: React.FC<{
  rect: Rect;
  pointer: HighlightBox;
  pointerIndex: number;
  pointerCount: number;
  focus: string;
  enter: number;
  frame: number;
}> = ({rect, pointer, pointerIndex, pointerCount, focus, enter, frame}) => {
  const panelX = (1 - enter) * 42;
  const label = trim(cleanLabel(pointer.label || focus || 'Key point'), 92);

  return (
    <div
      style={{
        position: 'absolute',
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
        borderRadius: 14,
        border: `2px solid ${LINE}`,
        background: 'rgba(255, 255, 255, 0.94)',
        boxShadow: '0 24px 58px rgba(24, 34, 50, 0.14)',
        padding: 30,
        opacity: enter,
        transform: `translateX(${panelX}px)`,
        overflow: 'hidden',
      }}
    >
      <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
        <div style={{color: SAFFRON, fontSize: 20, fontWeight: 900}}>
          Point {String(pointerIndex + 1).padStart(2, '0')}
        </div>
        <div style={{color: MUTED, fontSize: 18, fontWeight: 800}}>
          {pointerIndex + 1}/{pointerCount}
        </div>
      </div>
      <div
        style={{
          marginTop: 16,
          fontSize: rect.width > 700 ? 42 : 34,
          lineHeight: 1.06,
          fontWeight: 900,
        }}
      >
        {label}
      </div>
      <MotionGraphic frame={frame} />
      <div style={{marginTop: 24, fontSize: 20, fontWeight: 900, color: INK}}>
        What this means
      </div>
      <div
        style={{
          marginTop: 10,
          fontSize: rect.width > 700 ? 25 : 21,
          lineHeight: 1.32,
          color: MUTED,
          fontFamily: 'Georgia, serif',
        }}
      >
        {takeaway(label)}
      </div>
      <div style={{position: 'absolute', left: 30, right: 30, bottom: 26, display: 'flex', gap: 12}}>
        {['Fix', 'Prioritize', 'Measure'].map((chip) => (
          <div
            key={chip}
            style={{
              padding: '10px 16px',
              borderRadius: 8,
              border: '1px solid #ffd1bb',
              color: SAFFRON,
              background: '#fff4ed',
              fontWeight: 900,
              fontSize: 18,
            }}
          >
            {chip}
          </div>
        ))}
      </div>
    </div>
  );
};

const MotionGraphic: React.FC<{frame: number}> = ({frame}) => {
  const bars = [0.42, 0.7, 0.56, 0.86, 0.64];
  const wave = (offset: number) => 0.5 + Math.sin(frame / 18 + offset) * 0.5;

  return (
    <div
      style={{
        position: 'relative',
        height: 154,
        marginTop: 30,
        borderRadius: 12,
        border: '1px solid #e3ebf5',
        background: '#f8fafc',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          position: 'absolute',
          left: 24,
          right: 24,
          bottom: 24,
          height: 2,
          background: '#d8e2ef',
        }}
      />
      {bars.map((height, index) => {
        const grow = 0.74 + wave(index * 0.8) * 0.26;
        return (
          <div
            key={index}
            style={{
              position: 'absolute',
              left: 28 + index * 54,
              bottom: 24,
              width: 28,
              height: 92 * height * grow,
              borderRadius: 7,
              background: index === bars.length - 1 ? SAFFRON : '#ffbc7d',
            }}
          />
        );
      })}
      <div
        style={{
          position: 'absolute',
          right: 32,
          top: 30 + wave(1.5) * 10,
          width: 98,
          height: 64,
        }}
      >
        <div
          style={{
            position: 'absolute',
            left: 6,
            bottom: 8,
            width: 82,
            height: 30,
            borderRadius: 18,
            background: '#e8f6ff',
            border: '2px solid #1686ff',
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: 28,
            bottom: 20,
            width: 42,
            height: 42,
            borderRadius: '50%',
            background: '#e8f6ff',
            border: '2px solid #1686ff',
          }}
        />
        <div
          style={{
            position: 'absolute',
            right: 4,
            top: 0,
            width: 22,
            height: 22,
            borderRadius: '50%',
            background: GOLD,
            boxShadow: '0 0 0 8px rgba(243, 174, 50, 0.18)',
          }}
        />
      </div>
    </div>
  );
};

const PointerStrip: React.FC<{
  rect: Rect;
  pageNumber: number;
  total: number;
  pointer: HighlightBox;
  pointerIndex: number;
  pointerCount: number;
}> = ({rect, pageNumber, total, pointer, pointerIndex, pointerCount}) => {
  return (
    <div
      style={{
        position: 'absolute',
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
        display: 'flex',
        alignItems: 'center',
        borderRadius: 10,
        border: `1px solid ${LINE}`,
        background: 'rgba(255,255,255,0.92)',
        padding: '0 20px',
        gap: 18,
        fontSize: 18,
        fontWeight: 800,
      }}
    >
      <span style={{color: SAFFRON}}>
        Page {pageNumber} / {total}
      </span>
      <span style={{flex: 1, color: INK}}>
        Point {pointerIndex + 1}: {trim(cleanLabel(pointer.label), 76)}
      </span>
      {Array.from({length: Math.min(pointerCount, 6)}, (_, index) => (
        <span
          key={index}
          style={{
            width: 28,
            height: 28,
            borderRadius: 7,
            display: 'grid',
            placeItems: 'center',
            background: index === pointerIndex ? SAFFRON : '#eef2f7',
            color: index === pointerIndex ? '#ffffff' : MUTED,
            fontSize: 15,
          }}
        >
          {index + 1}
        </span>
      ))}
    </div>
  );
};

const WhiteFlash: React.FC<{frame: number; durationFrames: number}> = ({
  frame,
  durationFrames,
}) => {
  const opening = interpolate(frame, [0, 18], [0.82, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const closing = interpolate(frame, [durationFrames - 16, durationFrames], [0, 0.72], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const opacity = Math.max(opening, closing);

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        background: '#ffffff',
        opacity,
        pointerEvents: 'none',
      }}
    />
  );
};

const EmptyState = () => (
  <AbsoluteFill
    style={{
      display: 'grid',
      placeItems: 'center',
      fontSize: 48,
      fontWeight: 900,
      color: INK,
    }}
  >
    Report walkthrough
  </AbsoluteFill>
);

const getLayout = (width: number, height: number): Layout => {
  if (height > width) {
    return {
      titleTop: 78,
      titleLeft: 62,
      titleWidth: 900,
      titleSize: 48,
      page: {left: 62, top: 214, width: 956, height: 1018},
      panel: {left: 78, top: 1288, width: 924, height: 468},
      pointStrip: {left: 78, top: 1792, width: 924, height: 66},
    };
  }

  return {
    titleTop: 54,
    titleLeft: 68,
    titleWidth: 1200,
    titleSize: 48,
    page: {left: 68, top: 148, width: 1186, height: 804},
    panel: {left: 1290, top: 162, width: 560, height: 690},
    pointStrip: {left: 68, top: 980, width: 1782, height: 62},
  };
};

const cameraForPointer = (
  page: PageData,
  viewport: Rect,
  active: HighlightBox,
  previous: HighlightBox | undefined,
  pointerProgress: number,
  frame: number,
): CameraState => {
  const start = cameraState(page, viewport, previous);
  const end = cameraState(page, viewport, active);
  const mix = interpolate(pointerProgress, [0, 0.34], [0, 1], {
    easing: Easing.bezier(0.45, 0, 0.2, 1),
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const breath = 1 + Math.sin(frame / 24) * 0.004;

  return {
    x: lerp(start.x, end.x, mix),
    y: lerp(start.y, end.y, mix),
    scale: lerp(start.scale, end.scale, mix) * breath,
  };
};

const cameraState = (
  page: PageData,
  viewport: Rect,
  target: HighlightBox | undefined,
): CameraState => {
  const baseScale = Math.min(
    (viewport.width * 0.78) / Math.max(1, page.width),
    (viewport.height * 0.92) / Math.max(1, page.height),
  );

  if (!target) {
    return {
      x: (viewport.width - page.width * baseScale) / 2,
      y: (viewport.height - page.height * baseScale) / 2,
      scale: baseScale,
    };
  }

  const targetWidth = Math.max(1, target.x1 - target.x0);
  const targetHeight = Math.max(1, target.y1 - target.y0);
  const targetScale = Math.min(
    (viewport.width * 0.62) / targetWidth,
    (viewport.height * 0.29) / targetHeight,
  );
  const scale = clamp(targetScale, baseScale * 1.44, baseScale * 2.45);
  const focusX = ((target.x0 + target.x1) / 2) * scale;
  const focusY = ((target.y0 + target.y1) / 2) * scale;
  const scaledW = page.width * scale;
  const scaledH = page.height * scale;
  const minX = Math.min(24, viewport.width - scaledW - 24);
  const maxX = 24;
  const minY = Math.min(28, viewport.height - scaledH - 28);
  const maxY = 28;

  return {
    x: clamp(viewport.width * 0.5 - focusX, minX, maxX),
    y: clamp(viewport.height * 0.43 - focusY, minY, maxY),
    scale,
  };
};

const normalizedHighlights = (page: PageData): HighlightBox[] => {
  const highlights = (page.highlights || [])
    .filter((item) => item.x1 > item.x0 && item.y1 > item.y0)
    .slice(0, 6);

  if (highlights.length > 0) {
    return highlights;
  }

  return [0.18, 0.42, 0.66].map((top, index) => ({
    x0: page.width * 0.15,
    y0: page.height * top,
    x1: page.width * 0.85,
    y1: page.height * (top + 0.075),
    label: index === 0 ? page.focus : `Supporting point ${index + 1}`,
  }));
};

const highlightOpacity = (progress: number) => {
  if (progress < 0.16) {
    return progress / 0.16;
  }
  if (progress > 0.78) {
    return Math.max(0, (1 - progress) / 0.22);
  }
  return 1;
};

const takeaway = (label: string) =>
  `This section highlights ${trim(label, 54)}. Confirm the gap, prioritize the highest-impact action, and measure the result weekly.`;

const cleanLabel = (text: string) =>
  (text || '')
    .replace(/report visuals/gi, 'report section')
    .replace(/visual section/gi, 'report section')
    .replace(/visual context/gi, 'report context')
    .replace(/visuals/gi, 'sections')
    .replace(/\s+/g, ' ')
    .trim();

const trim = (text: string, limit: number) => {
  const cleaned = cleanLabel(text);
  if (cleaned.length <= limit) {
    return cleaned;
  }
  return `${cleaned.slice(0, Math.max(0, limit - 3)).trim()}...`;
};

const clamp = (value: number, lower: number, upper: number) =>
  Math.max(lower, Math.min(upper, value));

const lerp = (start: number, end: number, amount: number) =>
  start + (end - start) * clamp(amount, 0, 1);
