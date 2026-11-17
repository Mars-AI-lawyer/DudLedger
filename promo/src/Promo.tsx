import React from 'react';
import {AbsoluteFill, Audio, Composition, Sequence, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {C, FONT, clamp} from './lib';
import {SceneAI, SceneCalculate, SceneFinale, SceneImport, SceneIssues, SceneLocal, SceneOpening, SceneVoucher, TransitionWipe} from './scenes';

const SCENES = [SceneOpening, SceneImport, SceneVoucher, SceneCalculate, SceneIssues, SceneAI, SceneLocal, SceneFinale];

const Film: React.FC = () => {
  const frame = useCurrentFrame();
  const volume = interpolate(frame, [0, 12, 900, 945], [0, 0.82, 0.82, 0], clamp);
  return (
    <AbsoluteFill style={{background: C.deep, fontFamily: FONT, color: C.ink}}>
      <Audio src={staticFile('soundtrack.wav')} volume={volume} />
      {SCENES.map((Scene, index) => <Sequence key={index} from={index * 120} durationInFrames={120}><Scene /></Sequence>)}
      {[1, 2, 3, 4, 5, 6, 7].map((boundary) => <Sequence key={boundary} from={boundary * 120 - 9} durationInFrames={18}><TransitionWipe index={boundary} /></Sequence>)}
      <svg width="1920" height="1080" style={{position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 110}}>
        <defs><radialGradient id="vignette" cx="50%" cy="46%" r="72%"><stop offset="65%" stopColor="#000" stopOpacity="0" /><stop offset="100%" stopColor="#000" stopOpacity=".13" /></radialGradient></defs>
        <rect width="1920" height="1080" fill="url(#vignette)" />
      </svg>
    </AbsoluteFill>
  );
};

export const Promo: React.FC = () => (
  <Composition id="DudLedger" component={Film} durationInFrames={960} fps={30} width={1920} height={1080} defaultProps={{}} />
);
