import {CalculateMetadataFunction, Composition} from 'remotion';
import {ReportWalkthrough, ReportWalkthroughProps} from './ReportWalkthrough';

const defaultProps: ReportWalkthroughProps = {
  title: 'Report walkthrough',
  brandName: '',
  avatarMode: 'off',
  prospectLabel: 'Report',
  videoFormat: 'horizontal',
  fps: 60,
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

  return {
    durationInFrames: Math.ceil(durationSeconds * fps),
    fps,
    width: vertical ? 1080 : 1920,
    height: vertical ? 1920 : 1080,
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
