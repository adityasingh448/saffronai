import React from 'react';
import {Audio} from '@remotion/media';
import {loadFont} from '@remotion/google-fonts/Inter';
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
  renderQuality: string;
  renderWidth: number;
  renderHeight: number;
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
const INK = '#171f2f';
const MUTED = '#66758a';
const LINE = '#d8e2ef';
const WHITE = '#ffffff';
const {fontFamily: interFontFamily} = loadFont('normal', {
  weights: ['400', '500', '600', '700', '800', '900'],
  subsets: ['latin'],
});
const FONT_FAMILY = `${interFontFamily}, Arial, sans-serif`;

export const ReportWalkthrough: React.FC<ReportWalkthroughProps> = (props) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const layout = getLayout(width, height);
  const bgDrift = Math.sin(frame / (fps * 3.8));

  return (
    <AbsoluteFill
      style={{
        backgroundColor: '#f5f8fc',
        fontFamily: FONT_FAMILY,
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
            fontSize: Math.max(10, layout.titleSize * 0.42),
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
    pointerProgress,
    frame,
  );
  const cutOpacity =
    pointerIndex > 0
      ? interpolate(pointerProgress, [0, 0.08, 0.2], [0.62, 0.18, 0], {
          easing: Easing.out(Easing.cubic),
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        })
      : 0;

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
          background: 'rgba(255, 255, 255, 0.54)',
          border: '1px solid rgba(255, 255, 255, 0.62)',
          boxShadow: '0 30px 82px rgba(24, 34, 50, 0.20), 0 0 0 1px rgba(216, 226, 239, 0.72)',
          backdropFilter: 'blur(18px) saturate(1.16)',
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
        </div>
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background:
              'linear-gradient(180deg, rgba(255,250,246,0.58), transparent 13%, transparent 86%, rgba(255,250,246,0.58))',
            opacity: 0.58,
            pointerEvents: 'none',
          }}
        />
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background: WHITE,
            opacity: cutOpacity,
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
}> = ({rect, pointer, pointerIndex, pointerCount, focus, enter}) => {
  const panelX = (1 - enter) * 42;
  const label = trim(cleanLabel(pointer.label || focus || 'Key point'), 92);
  const padding = clamp(rect.width * 0.055, 14, 30);
  const eyebrowSize = clamp(rect.width * 0.034, 10, 20);
  const headlineSize = clamp(rect.width * 0.062, 16, 34);
  const bodySize = clamp(rect.width * 0.041, 12, 21);

  return (
    <div
      style={{
        position: 'absolute',
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
        borderRadius: 14,
        border: '1px solid rgba(255, 255, 255, 0.66)',
        background: 'rgba(255, 255, 255, 0.62)',
        boxShadow: '0 26px 68px rgba(24, 34, 50, 0.16), inset 0 1px 0 rgba(255,255,255,0.78)',
        backdropFilter: 'blur(18px) saturate(1.14)',
        padding,
        opacity: enter,
        transform: `translateX(${panelX}px)`,
        overflow: 'hidden',
        fontFamily: FONT_FAMILY,
      }}
    >
      <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
        <div style={{color: SAFFRON, fontSize: eyebrowSize, fontWeight: 900}}>
          Point {String(pointerIndex + 1).padStart(2, '0')}
        </div>
        <div style={{color: MUTED, fontSize: Math.max(10, eyebrowSize * 0.9), fontWeight: 800}}>
          {pointerIndex + 1}/{pointerCount}
        </div>
      </div>
      <div
        style={{
          marginTop: Math.max(8, padding * 0.55),
          fontSize: headlineSize,
          lineHeight: 1.06,
          fontWeight: 900,
        }}
      >
        {label}
      </div>
      <div
        style={{
          marginTop: Math.max(18, padding * 1.2),
          width: '100%',
          height: 1,
          background: 'rgba(216, 226, 239, 0.92)',
        }}
      />
      <div style={{marginTop: Math.max(14, padding * 0.82), fontSize: eyebrowSize, fontWeight: 900, color: SAFFRON}}>
        Summary
      </div>
      <div
        style={{
          marginTop: 10,
          fontSize: bodySize,
          lineHeight: 1.38,
          color: INK,
          fontFamily: FONT_FAMILY,
          fontWeight: 650,
        }}
      >
        {summaryText(label)}
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
  const baseSize = clamp(rect.height * 0.29, 10, 18);
  const dotSize = clamp(rect.height * 0.42, 18, 28);

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
        padding: `0 ${Math.max(10, rect.height * 0.3)}px`,
        gap: Math.max(8, rect.height * 0.22),
        fontSize: baseSize,
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
            width: dotSize,
            height: dotSize,
            borderRadius: 7,
            display: 'grid',
            placeItems: 'center',
            background: index === pointerIndex ? SAFFRON : '#eef2f7',
            color: index === pointerIndex ? WHITE : MUTED,
            fontSize: Math.max(9, dotSize * 0.52),
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
    const margin = clamp(width * 0.055, 20, 62);
    const headerH = clamp(height * 0.09, 64, 150);
    const panelH = clamp(height * 0.24, 178, 468);
    const stripH = clamp(height * 0.04, 42, 66);
    const gap = clamp(height * 0.018, 18, 34);
    const panelTop = height - margin - stripH - gap - panelH;
    const pageTop = headerH;
    const pageH = Math.max(240, panelTop - gap - pageTop);

    return {
      titleTop: margin * 0.55,
      titleLeft: margin,
      titleWidth: width - margin * 2,
      titleSize: clamp(width * 0.045, 24, 48),
      page: {left: margin, top: pageTop, width: width - margin * 2, height: pageH},
      panel: {left: margin, top: panelTop, width: width - margin * 2, height: panelH},
      pointStrip: {left: margin, top: height - margin - stripH, width: width - margin * 2, height: stripH},
    };
  }

  const margin = clamp(width * 0.032, 18, 68);
  const headerH = clamp(height * 0.12, 56, 128);
  const stripH = clamp(height * 0.072, 34, 62);
  const gap = clamp(width * 0.018, 12, 36);
  const panelW = clamp(width * 0.285, 220, 560);
  const pageTop = headerH;
  const pageH = Math.max(260, height - pageTop - stripH - margin * 1.7);
  const pageW = Math.max(300, width - margin * 2 - gap - panelW);

  return {
    titleTop: margin * 0.55,
    titleLeft: margin,
    titleWidth: width - margin * 2,
    titleSize: clamp(width * 0.025, 18, 38),
    page: {left: margin, top: pageTop, width: pageW, height: pageH},
    panel: {left: margin + pageW + gap, top: pageTop, width: panelW, height: pageH},
    pointStrip: {left: margin, top: height - margin * 0.82 - stripH, width: width - margin * 2, height: stripH},
  };
};

const cameraForPointer = (
  page: PageData,
  viewport: Rect,
  active: HighlightBox,
  pointerProgress: number,
  frame: number,
): CameraState => {
  const fullPage = cameraState(page, viewport, undefined);
  const focus = cameraState(page, viewport, active);
  const zoomIn = interpolate(pointerProgress, [0.16, 0.44], [0, 1], {
    easing: Easing.bezier(0.45, 0, 0.2, 1),
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const zoomOut = interpolate(pointerProgress, [0.82, 0.98], [0, 1], {
    easing: Easing.bezier(0.45, 0, 0.2, 1),
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const focusMix = zoomIn * (1 - zoomOut);
  const breath = 1 + Math.sin(frame / 30) * 0.0025 * focusMix;

  return {
    x: lerp(fullPage.x, focus.x, focusMix),
    y: lerp(fullPage.y, focus.y, focusMix),
    scale: lerp(fullPage.scale, focus.scale, focusMix) * breath,
  };
};

const cameraState = (
  page: PageData,
  viewport: Rect,
  target: HighlightBox | undefined,
): CameraState => {
  const baseScale = Math.min(
    (viewport.width * 0.94) / Math.max(1, page.width),
    (viewport.height * 0.94) / Math.max(1, page.height),
  );

  if (!target) {
    return {
      x: (viewport.width - page.width * baseScale) / 2,
      y: (viewport.height - page.height * baseScale) / 2,
      scale: baseScale,
    };
  }

  const targetWidth = Math.max(page.width * 0.42, target.x1 - target.x0);
  const targetHeight = Math.max(page.height * 0.06, target.y1 - target.y0);
  const targetScale = Math.min(
    (viewport.width * 0.78) / targetWidth,
    (viewport.height * 0.44) / targetHeight,
  );
  const scale = clamp(targetScale, baseScale * 1.55, baseScale * 2.6);
  const focusX = ((target.x0 + target.x1) / 2) * scale;
  const focusY = ((target.y0 + target.y1) / 2) * scale;
  const scaledW = page.width * scale;
  const scaledH = page.height * scale;
  const edgeX = Math.max(8, viewport.width * 0.025);
  const edgeY = Math.max(8, viewport.height * 0.025);
  const minX = Math.min(edgeX, viewport.width - scaledW - edgeX);
  const maxX = edgeX;
  const minY = Math.min(edgeY, viewport.height - scaledH - edgeY);
  const maxY = edgeY;

  return {
    x: clamp(viewport.width * 0.5 - focusX, minX, maxX),
    y: clamp(viewport.height * 0.46 - focusY, minY, maxY),
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

const summaryText = (label: string) =>
  `Focus on ${trim(label, 54)}. This is the part to review first, convert into one clear action, and track against a measurable result.`;

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
