import React, {CSSProperties, ReactNode} from 'react';
import {Easing, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';

export const C = {
  canvas: '#FAF9F5',
  ivory: '#F3F0E8',
  paper: '#FFFFFF',
  sunken: '#ECE9E1',
  ink: '#30302C',
  deep: '#202725',
  soft: '#625F57',
  muted: '#8A877E',
  line: '#DCD8CE',
  accent: '#A65036',
  accentLight: '#D58B70',
  accentSoft: '#F1E3DD',
  sage: '#C7D3C3',
  sageDeep: '#5E7867',
  gold: '#B49B6A',
};

export const FONT = '"PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif';
export const LATIN = '"Avenir Next", "Helvetica Neue", Arial, sans-serif';
export const MONO = '"SFMono-Regular", "Menlo", monospace';

export const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
export const ease = (f: number, start: number, end: number) => interpolate(f, [start, end], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
export const out = (f: number, start: number, end: number) => interpolate(f, [start, end], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
export const pop = (f: number, delay = 0) => spring({frame: Math.max(0, f - delay), fps: 30, config: {damping: 18, stiffness: 150, mass: 0.8}, durationInFrames: 40});

export const KineticText: React.FC<{
  text: string;
  delay?: number;
  size?: number;
  color?: string;
  weight?: number;
  style?: CSSProperties;
  latin?: boolean;
  stagger?: number;
}> = ({text, delay = 0, size = 96, color = C.ink, weight = 650, style, latin = false, stagger = 1.25}) => {
  const frame = useCurrentFrame();
  const chars = Array.from(text);
  return (
    <div style={{display: 'flex', alignItems: 'baseline', overflow: 'hidden', fontFamily: latin ? LATIN : FONT, fontSize: size, fontWeight: weight, letterSpacing: latin ? '-0.055em' : '-0.045em', lineHeight: 1.06, color, ...style}}>
      {chars.map((char, i) => {
        const p = out(frame, delay + i * stagger, delay + i * stagger + 18);
        return <span key={i} style={{display: 'inline-block', whiteSpace: char === ' ' ? 'pre' : 'normal', opacity: p, transform: `translateY(${(1 - p) * size * 0.85}px) rotateX(${(1 - p) * -65}deg)`, transformOrigin: '50% 100%'}}>{char}</span>;
      })}
    </div>
  );
};

export const FadeUp: React.FC<{delay?: number; children: ReactNode; style?: CSSProperties; distance?: number}> = ({delay = 0, children, style, distance = 32}) => {
  const f = useCurrentFrame();
  const p = out(f, delay, delay + 22);
  return <div style={{opacity: p, transform: `translateY(${(1 - p) * distance}px)`, ...style}}>{children}</div>;
};

export const Eyebrow: React.FC<{children: ReactNode; dark?: boolean; style?: CSSProperties}> = ({children, dark = false, style}) => (
  <div style={{display: 'flex', alignItems: 'center', gap: 14, color: dark ? C.sage : C.accent, fontFamily: MONO, fontSize: 20, fontWeight: 600, letterSpacing: '0.16em', textTransform: 'uppercase', ...style}}>
    <span style={{width: 34, height: 2, background: 'currentColor'}} />
    {children}
  </div>
);

export const Grid: React.FC<{dark?: boolean; opacity?: number; perspective?: boolean}> = ({dark = false, opacity = 0.34, perspective = false}) => {
  const f = useCurrentFrame();
  const color = dark ? C.sage : C.ink;
  return (
    <div style={{position: 'absolute', inset: '-30%', opacity, transform: perspective ? `perspective(800px) rotateX(63deg) translateY(${180 + f * 1.2}px) scale(1.25)` : `translate(${-(f % 60)}px, ${-(f % 60)}px)`, transformOrigin: '50% 70%'}}>
      <svg width="100%" height="100%" viewBox="0 0 2400 1600" preserveAspectRatio="xMidYMid slice">
        <defs><pattern id={dark ? 'grid-dark' : 'grid-light'} width="60" height="60" patternUnits="userSpaceOnUse"><path d="M60 0H0V60" fill="none" stroke={color} strokeOpacity="0.22" strokeWidth="1" /></pattern></defs>
        <rect width="100%" height="100%" fill={`url(#${dark ? 'grid-dark' : 'grid-light'})`} />
      </svg>
    </div>
  );
};

export const FluidBlob: React.FC<{color?: string; x?: number; y?: number; size?: number; dark?: boolean; opacity?: number}> = ({color = C.sage, x = 1100, y = -180, size = 900, opacity = 0.8}) => {
  const f = useCurrentFrame();
  const morph = Math.sin(f / 32) * 36;
  return (
    <svg width={size} height={size} viewBox="0 0 600 600" style={{position: 'absolute', left: x, top: y, opacity, filter: 'blur(1px)', transform: `rotate(${f * 0.12}deg) scale(${1 + Math.sin(f / 45) * 0.04})`}}>
      <defs>
        <radialGradient id={`fluid-${color.replace('#', '')}`} cx="35%" cy="30%">
          <stop offset="0%" stopColor="#FFFFFF" stopOpacity="0.8" />
          <stop offset="45%" stopColor={color} stopOpacity="0.85" />
          <stop offset="100%" stopColor={color} stopOpacity="0.12" />
        </radialGradient>
        <filter id="goo"><feGaussianBlur stdDeviation="14" /><feColorMatrix values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 22 -10" /></filter>
      </defs>
      <g filter="url(#goo)">
        <path fill={`url(#fluid-${color.replace('#', '')})`} d={`M ${300 + morph} 58 C 420 40 544 148 526 276 C 510 394 450 526 310 534 C 176 542 58 456 62 320 C 66 184 172 74 ${300 + morph} 58Z`} />
        <circle cx={150 + Math.sin(f / 24) * 24} cy={400 + Math.cos(f / 30) * 25} r="90" fill={color} opacity="0.55" />
        <circle cx={470 + Math.cos(f / 28) * 20} cy={155 + Math.sin(f / 25) * 22} r="72" fill={color} opacity="0.42" />
      </g>
    </svg>
  );
};

const seeded = (n: number) => {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
};

export const Particles: React.FC<{count?: number; dark?: boolean; converge?: number; opacity?: number}> = ({count = 54, dark = false, converge = 0, opacity = 1}) => {
  const f = useCurrentFrame();
  const {width, height} = useVideoConfig();
  return (
    <svg width={width} height={height} style={{position: 'absolute', inset: 0, opacity, pointerEvents: 'none'}}>
      {new Array(count).fill(null).map((_, i) => {
        const sx = seeded(i + 2) * width;
        const sy = seeded(i + 80) * height;
        const driftX = Math.sin(f / 30 + i) * 24;
        const driftY = Math.cos(f / 38 + i * 0.7) * 20;
        const x = sx + driftX + (width / 2 - sx) * converge;
        const y = sy + driftY + (height / 2 - sy) * converge;
        const r = 1.6 + seeded(i + 160) * 4.2;
        const pulse = 0.25 + 0.65 * (0.5 + 0.5 * Math.sin(f / 10 + i));
        return <circle key={i} cx={x} cy={y} r={r * (1 - converge * 0.4)} fill={i % 7 === 0 ? C.accentLight : dark ? C.sage : C.ink} opacity={pulse} />;
      })}
    </svg>
  );
};

export const Screen: React.FC<{
  src: string;
  width: number;
  height?: number;
  label?: string;
  style?: CSSProperties;
  imageStyle?: CSSProperties;
  chrome?: boolean;
}> = ({src, width, height, label = 'DUDLEDGER / LOCAL WORKSPACE', style, imageStyle, chrome = true}) => {
  const h = height ?? width * 0.617;
  return (
    <div style={{width, height: h + (chrome ? 46 : 0), background: C.paper, border: `1px solid ${C.line}`, borderRadius: 20, overflow: 'hidden', boxShadow: '0 38px 100px rgba(39,35,28,.19), 0 4px 18px rgba(39,35,28,.08)', ...style}}>
      {chrome && <div style={{height: 46, display: 'flex', alignItems: 'center', padding: '0 18px', gap: 8, background: '#F4F2EC', borderBottom: `1px solid ${C.line}`}}>
        {[C.accentLight, C.gold, C.sageDeep].map((color) => <span key={color} style={{width: 9, height: 9, borderRadius: 9, background: color, opacity: 0.85}} />)}
        <span style={{marginLeft: 12, fontFamily: MONO, fontSize: 13, letterSpacing: '0.12em', color: C.muted}}>{label}</span>
        <span style={{marginLeft: 'auto', width: 74, height: 7, borderRadius: 6, background: C.line}} />
      </div>}
      <Img src={staticFile(src)} style={{width: '100%', height: h, objectFit: 'fill', display: 'block', ...imageStyle}} />
    </div>
  );
};

export const Cursor: React.FC<{x: number; y: number; pressed?: boolean; dark?: boolean}> = ({x, y, pressed = false, dark = false}) => (
  <div style={{position: 'absolute', left: x, top: y, width: 42, height: 52, filter: 'drop-shadow(0 5px 7px rgba(0,0,0,.25))', transform: `scale(${pressed ? 0.84 : 1})`, transformOrigin: '8% 8%', zIndex: 30}}>
    <svg viewBox="0 0 42 52" width="42" height="52"><path d="M5 3L36 31L23 33L17 47L5 3Z" fill={dark ? C.deep : C.paper} stroke={dark ? C.paper : C.deep} strokeWidth="2.5" strokeLinejoin="round" /></svg>
  </div>
);

export const Icon: React.FC<{name: 'table' | 'database' | 'voucher' | 'calc' | 'issue' | 'spark' | 'shield' | 'search'; size?: number; color?: string; strokeWidth?: number}> = ({name, size = 32, color = 'currentColor', strokeWidth = 1.8}) => {
  const paths: Record<string, ReactNode> = {
    table: <><rect x="4" y="5" width="26" height="24" rx="3" /><path d="M4 12h26M12 5v24M21 5v24M4 20h26" /></>,
    database: <><ellipse cx="17" cy="8" rx="12" ry="5" /><path d="M5 8v9c0 2.8 5.4 5 12 5s12-2.2 12-5V8M5 17v9c0 2.8 5.4 5 12 5s12-2.2 12-5v-9" /></>,
    voucher: <><path d="M8 3h14l6 6v23H8z" /><path d="M22 3v7h6M13 16h10M13 22h10M13 28h6" /></>,
    calc: <><rect x="6" y="3" width="22" height="29" rx="4" /><path d="M10 7h14v6H10zM11 18h1M17 18h1M23 18h1M11 24h1M17 24h1M23 24h1M11 29h1M17 29h1M23 29h1" /></>,
    issue: <><path d="M5 7h24v19H16l-7 5v-5H5z" /><path d="M11 14h12M11 19h8" /></>,
    spark: <><path d="M17 2l2.8 10.2L30 15l-10.2 2.8L17 28l-2.8-10.2L4 15l10.2-2.8z" /><path d="M27 25l1 4 4 1-4 1-1 4-1-4-4-1 4-1z" /></>,
    shield: <><path d="M17 3l12 5v9c0 8-5 13-12 16C10 30 5 25 5 17V8z" /><path d="M11 17l4 4 8-9" /></>,
    search: <><circle cx="14" cy="14" r="9" /><path d="M21 21l9 9" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 34 36" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
};

export const Chip: React.FC<{icon?: Parameters<typeof Icon>[0]['name']; children: ReactNode; dark?: boolean; style?: CSSProperties}> = ({icon, children, dark = false, style}) => (
  <div style={{display: 'inline-flex', alignItems: 'center', gap: 10, padding: '12px 18px', borderRadius: 999, background: dark ? 'rgba(255,255,255,.07)' : 'rgba(255,255,255,.72)', border: `1px solid ${dark ? 'rgba(255,255,255,.16)' : C.line}`, color: dark ? C.ivory : C.soft, fontSize: 23, fontWeight: 500, backdropFilter: 'blur(12px)', ...style}}>
    {icon && <Icon name={icon} size={25} color={dark ? C.sage : C.accent} />}{children}
  </div>
);

export const SceneLife: React.FC<{children: ReactNode; background?: string; style?: CSSProperties}> = ({children, background = C.canvas, style}) => {
  const f = useCurrentFrame();
  const enter = out(f, 0, 16);
  const leave = ease(f, 108, 120);
  return <div style={{position: 'absolute', inset: 0, overflow: 'hidden', background, opacity: enter * (1 - leave), transform: `scale(${1 + leave * 0.035})`, filter: `blur(${leave * 5}px)`, ...style}}>{children}</div>;
};

export const CountUp: React.FC<{value: number; start?: number; end?: number; prefix?: string; style?: CSSProperties}> = ({value, start = 15, end = 55, prefix = '', style}) => {
  const f = useCurrentFrame();
  const p = out(f, start, end);
  const formatted = (value * p).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  return <div style={{fontFamily: LATIN, fontVariantNumeric: 'tabular-nums', ...style}}>{prefix}{formatted}</div>;
};
