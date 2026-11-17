const fs = require('fs');
const path = require('path');

const SR = 48000;
const DURATION = 32;
const N = SR * DURATION;
const left = new Float32Array(N);
const right = new Float32Array(N);
let noiseState = 0x12345678;
function noise() {
  noiseState ^= noiseState << 13;
  noiseState ^= noiseState >>> 17;
  noiseState ^= noiseState << 5;
  return ((noiseState >>> 0) / 2147483648) - 1;
}
function panGains(pan) {
  const angle = (pan + 1) * Math.PI / 4;
  return [Math.cos(angle), Math.sin(angle)];
}
function add(start, duration, fn, amp = 0.2, pan = 0) {
  const begin = Math.max(0, Math.floor(start * SR));
  const end = Math.min(N, Math.floor((start + duration) * SR));
  const [lg, rg] = panGains(pan);
  for (let i = begin; i < end; i++) {
    const t = (i - begin) / SR;
    const value = fn(t, duration, i) * amp;
    left[i] += value * lg;
    right[i] += value * rg;
  }
}
function kick(start, amp = 0.55) {
  let phase = 0;
  add(start, 0.48, (t) => {
    const freq = 43 + 105 * Math.exp(-t * 22);
    phase += 2 * Math.PI * freq / SR;
    return Math.sin(phase) * Math.exp(-t * 9) * Math.min(1, t * 450);
  }, amp);
  add(start, 0.055, (t) => noise() * Math.exp(-t * 85), amp * 0.11);
}
function hat(start, amp = 0.055, pan = 0) {
  let last = 0;
  add(start, 0.09, (t) => {
    const n = noise();
    const hp = n - last;
    last = n;
    return hp * Math.exp(-t * 58) * Math.min(1, t * 900);
  }, amp, pan);
}
function snap(start, amp = 0.15) {
  let last = 0;
  add(start, 0.18, (t) => {
    const n = noise();
    const band = (n - last) * 0.72;
    last = n;
    return (band + Math.sin(2 * Math.PI * 185 * t) * 0.25) * Math.exp(-t * 24);
  }, amp, 0.06);
}
function pluck(start, freq, amp = 0.11, pan = 0, duration = 0.8) {
  add(start, duration, (t) => {
    const env = Math.min(1, t * 180) * Math.exp(-t * 5.2);
    const tone = Math.sin(2 * Math.PI * freq * t) + 0.34 * Math.sin(2 * Math.PI * freq * 2 * t) + 0.12 * Math.sin(2 * Math.PI * freq * 3 * t);
    return tone * env;
  }, amp, pan);
  add(start + 0.25, duration, (t) => Math.sin(2 * Math.PI * freq * t) * Math.exp(-t * 6) * Math.min(1, t * 120), amp * 0.2, -pan * 0.7);
}
function bass(start, freq, amp = 0.14, duration = 0.42) {
  add(start, duration, (t) => {
    const env = Math.min(1, t * 40) * Math.min(1, (duration - t) * 12) * Math.exp(-t * 1.5);
    return (Math.sin(2 * Math.PI * freq * t) + 0.18 * Math.sin(2 * Math.PI * freq * 2 * t)) * env;
  }, amp);
}
function pad(start, freqs, amp = 0.026) {
  freqs.forEach((freq, index) => {
    add(start, 4.15, (t, d) => {
      const env = Math.min(1, t / 0.65) * Math.min(1, (d - t) / 0.9);
      const vibrato = 1 + 0.0018 * Math.sin(2 * Math.PI * 0.22 * t + index);
      return (Math.sin(2 * Math.PI * freq * vibrato * t) + 0.22 * Math.sin(2 * Math.PI * freq * 2 * t)) * env;
    }, amp, (index - 2) * 0.18);
  });
}
function whoosh(start, amp = 0.1) {
  let smooth = 0;
  add(start, 0.72, (t, d) => {
    smooth = smooth * 0.82 + noise() * 0.18;
    const x = t / d;
    const env = Math.pow(Math.sin(Math.PI * x), 1.5);
    return (smooth * 1.8 + Math.sin(2 * Math.PI * (180 + x * 900) * t) * 0.09) * env;
  }, amp, Math.sin(start) * 0.5);
}
function impact(start, amp = 0.24) {
  kick(start, amp * 1.4);
  add(start, 0.9, (t) => Math.sin(2 * Math.PI * 54 * t) * Math.exp(-t * 5.8), amp * 0.55);
  add(start, 0.35, (t) => noise() * Math.exp(-t * 14), amp * 0.17);
}
function click(start, amp = 0.08) {
  add(start, 0.07, (t) => (Math.sin(2 * Math.PI * 1100 * t) + noise() * 0.35) * Math.exp(-t * 70), amp, 0.28);
}

const chords = [
  [146.83, 174.61, 220.00, 261.63, 329.63],
  [116.54, 146.83, 174.61, 220.00, 293.66],
  [174.61, 220.00, 261.63, 329.63, 392.00],
  [130.81, 196.00, 220.00, 293.66, 329.63],
  [146.83, 174.61, 220.00, 261.63, 329.63],
  [116.54, 146.83, 174.61, 220.00, 293.66],
  [174.61, 220.00, 261.63, 329.63, 392.00],
  [146.83, 185.00, 220.00, 293.66, 329.63],
];
const roots = [73.42, 58.27, 87.31, 65.41, 73.42, 58.27, 87.31, 73.42];
const melody = [293.66, 349.23, 440.00, 523.25, 440.00, 392.00, 349.23, 293.66];

for (let scene = 0; scene < 8; scene++) {
  const start = scene * 4;
  pad(start, chords[scene], scene === 5 ? 0.032 : 0.025);
  if (scene > 0) {
    whoosh(start - 0.48, scene === 7 ? 0.14 : 0.09);
    impact(start, scene === 7 ? 0.25 : 0.18);
  }
  for (let beat = 0; beat < 8; beat++) {
    const time = start + beat * 0.5;
    const active = scene > 0 || beat >= 4;
    if (active && (beat === 0 || beat === 4 || (scene >= 2 && beat === 6))) kick(time, scene === 7 ? 0.38 : 0.32);
    if (active && (beat === 2 || beat === 6)) snap(time, scene === 5 ? 0.18 : 0.13);
    if (active) {
      hat(time + 0.25, 0.045, beat % 2 ? 0.35 : -0.35);
      if (scene >= 2) hat(time + 0.375, 0.022, -0.15);
    }
    if (active && beat % 2 === 0) bass(time, roots[scene] * (beat === 6 ? 1.5 : 1), scene === 5 ? 0.16 : 0.12);
    if ((scene === 0 && beat >= 4) || (scene > 0 && [1, 3, 6].includes(beat))) {
      const note = melody[(scene * 2 + beat) % melody.length] * (scene === 7 ? 1.5 : 1);
      pluck(time + 0.04, note, scene === 7 ? 0.10 : 0.065, beat % 2 ? 0.35 : -0.3);
    }
  }
}

// Opening sonic logo: particle rise, icon impact, light sweep and camera move.
whoosh(0.02, 0.12);
pluck(0.22, 293.66, 0.075, -0.3, 1.1);
pluck(0.42, 440.0, 0.085, 0.28, 1.2);
impact(0.70, 0.23);
pluck(0.78, 880.0, 0.065, 0, 1.3);
whoosh(1.68, 0.09);
pluck(1.90, 587.33, 0.075, 0.25, 1.0);

// Product-action sound cues, synchronized to the picture.
click(9.42, 0.10);
click(9.56, 0.08);
pluck(10.08, 587.33, 0.09, 0.25);
click(13.0, 0.07);
click(13.52, 0.07);
pluck(14.15, 659.25, 0.08, -0.2);
pluck(16.25, 440.0, 0.07, -0.3);
pluck(17.45, 523.25, 0.08, 0.25);
pluck(18.05, 659.25, 0.10, 0.1);
[20.2, 20.7, 21.2, 21.7, 22.2, 22.7].forEach((t, i) => pluck(t, 220 * Math.pow(2, i / 12), 0.035, -0.6 + i * 0.24, 0.5));
pluck(28.35, 587.33, 0.11, -0.25, 1.4);
pluck(28.85, 739.99, 0.10, 0.1, 1.5);
pluck(29.35, 880.0, 0.10, 0.35, 1.6);
pluck(30.0, 1174.66, 0.06, 0, 1.7);

let peak = 0;
for (let i = 0; i < N; i++) {
  const fadeIn = Math.min(1, i / (SR * 0.08));
  const fadeOut = Math.min(1, (N - i) / (SR * 1.0));
  left[i] = Math.tanh(left[i] * 1.25) * fadeIn * fadeOut;
  right[i] = Math.tanh(right[i] * 1.25) * fadeIn * fadeOut;
  peak = Math.max(peak, Math.abs(left[i]), Math.abs(right[i]));
}
const gain = 0.92 / Math.max(peak, 0.001);
const buffer = Buffer.alloc(44 + N * 4);
buffer.write('RIFF', 0);
buffer.writeUInt32LE(36 + N * 4, 4);
buffer.write('WAVE', 8);
buffer.write('fmt ', 12);
buffer.writeUInt32LE(16, 16);
buffer.writeUInt16LE(1, 20);
buffer.writeUInt16LE(2, 22);
buffer.writeUInt32LE(SR, 24);
buffer.writeUInt32LE(SR * 4, 28);
buffer.writeUInt16LE(4, 32);
buffer.writeUInt16LE(16, 34);
buffer.write('data', 36);
buffer.writeUInt32LE(N * 4, 40);
for (let i = 0; i < N; i++) {
  buffer.writeInt16LE(Math.round(left[i] * gain * 32767), 44 + i * 4);
  buffer.writeInt16LE(Math.round(right[i] * gain * 32767), 46 + i * 4);
}
const output = path.join(__dirname, 'assets', 'soundtrack.wav');
fs.writeFileSync(output, buffer);
console.log(`Wrote ${output} (${DURATION}s, stereo ${SR}Hz, peak ${(peak * gain).toFixed(3)})`);
