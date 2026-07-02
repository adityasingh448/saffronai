import {CalculateMetadataFunction, Composition} from 'remotion';
import {ReportWalkthrough, ReportWalkthroughProps} from './ReportWalkthrough';

const defaultProps: ReportWalkthroughProps = {
  title: 'Report walkthrough',
  brandName: '',
  avatarMode: 'off',
  prospectLabel: 'Report',
  videoFormat: 'horizontal',
  fps: 60,
  renderQuality: '1080p',
  renderWidth: 1920,
  renderHeight: 1080,
  durationSeconds: 10,
  audioSrc: '',
  pages: [],
};

const calculateMetadata: CalculateMetadataFunction<ReportWalkthroughProps> = ({
  props,
}) => {
  const vertical = props.videoFormat === 'vertical';
  const fps = Math.max(24, Math.min(90, Math.round(props.fps || 60)));
  const durationSeconds = Math.max(1, props.durationSeconds || 10);
  const fallbackWidth = vertical ? 1080 : 1920;
  const fallbackHeight = vertical ? 1920 : 1080;
  const width = Math.max(480, Math.round(props.renderWidth || fallbackWidth));
  const height = Math.max(480, Math.round(props.renderHeight || fallbackHeight));

  return {
    durationInFrames: Math.ceil(durationSeconds * fps),
    fps,
    width,
    height,
  };
};

export const RemotionRoot = () => {
  return (
    <Composition
      id="ReportWalkthrough"
      component={ReportWalkthrough}
      durationInFrames={600}
      fps={60}
      width={1920}
      height={1080}
      defaultProps={defaultProps}
      calculateMetadata={calculateMetadata}
    />
  );
};
