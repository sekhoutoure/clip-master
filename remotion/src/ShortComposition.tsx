import React from 'react';
import {
  AbsoluteFill,
  OffthreadVideo,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import captionData from './captions.json';

interface CaptionItem {
  id: string;
  text: string;
  start: number;
  end: number;
}

export const ShortComposition: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentTime = frame / fps;

  const data = captionData as any;
  const captions: CaptionItem[] = data.captions || [];
  const styleCfg = data.style || { fg: '#ee1d6c', bg: '#ffffff' };
  const hasBaseVideo = Boolean(data.project_id);
  const active = captions.find((c) => currentTime >= c.start && currentTime <= c.end);

  const pop = active
    ? spring({
        frame: Math.max(0, Math.round((currentTime - active.start) * fps)),
        fps,
        config: { damping: 12, stiffness: 260 },
      })
    : 0;

  const scale = interpolate(pop, [0, 1], [0.85, 1.0]);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: '#111111',
        justifyContent: 'center',
        alignItems: 'center',
        fontFamily: 'Impact, Anton, Arial Black, sans-serif',
      }}
    >
      {hasBaseVideo && (
        <AbsoluteFill>
          <OffthreadVideo src={staticFile('base.mp4')} />
        </AbsoluteFill>
      )}
      {active && (
        <div
          style={{
            position: 'absolute',
            bottom: 360,
            transform: `scale(${scale}) ${styleCfg.italic ? 'rotate(-2deg)' : ''}`,
            backgroundColor: styleCfg.outline ? 'transparent' : styleCfg.bg,
            color: styleCfg.fg,
            padding: styleCfg.outline ? '0 20px' : '16px 34px',
            borderRadius: styleCfg.outline ? 0 : 12,
            fontSize: styleCfg.outline ? 82 : 74,
            fontWeight: 900,
            fontStyle: styleCfg.italic ? 'italic' : 'normal',
            letterSpacing: 2,
            textTransform: 'uppercase',
            textAlign: 'center',
            maxWidth: 960,
            boxShadow: styleCfg.outline ? 'none' : '0 14px 40px rgba(0,0,0,0.55)',
            WebkitTextStroke: styleCfg.outline ? '12px #000000' : 'none',
            paintOrder: 'stroke fill',
            textShadow: styleCfg.outline ? '0 10px 24px rgba(0,0,0,0.9)' : 'none',
          }}
        >
          {active.text}
        </div>
      )}
    </AbsoluteFill>
  );
};
