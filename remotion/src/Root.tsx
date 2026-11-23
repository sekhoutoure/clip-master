import React from 'react';
import { Composition, registerRoot } from 'remotion';
import { ShortComposition } from './ShortComposition';
import captionData from './captions.json';

export const RemotionRoot: React.FC = () => {
  const data = captionData as any;
  const fps = Number(data.fps) || 30;
  const durationInFrames = Math.max(30, Number(data.durationInFrames) || 150);

  return (
    <Composition
      id="Short01"
      component={ShortComposition}
      durationInFrames={durationInFrames}
      fps={fps}
      width={1080}
      height={1920}
    />
  );
};

registerRoot(RemotionRoot);
