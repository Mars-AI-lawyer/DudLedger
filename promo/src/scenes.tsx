import React, {CSSProperties} from 'react';
import {Img, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {C, Chip, CountUp, Cursor, Eyebrow, FadeUp, FluidBlob, Grid, Icon, KineticText, LATIN, MONO, Particles, SceneLife, Screen, clamp, ease, out, pop} from './lib';

const abs = (left: number, top: number, extra: CSSProperties = {}): CSSProperties => ({position: 'absolute', left, top, ...extra});

export const SceneOpening: React.FC = () => {
  const f = useCurrentFrame();
  const reveal = out(f, 3, 24);
  const impact = out(f, 20, 38);
  const move = ease(f, 55, 82);
  const question = out(f, 72, 100);
  const size = interpolate(move, [0, 1], [390, 258], clamp);
  const x = interpolate(move, [0, 1], [765, 176], clamp);
  const y = interpolate(move, [0, 1], [238, 286], clamp);
  const cx = x + size / 2;
  const cy = y + size / 2;
  const flash = 1 - out(f, 20, 29);
  const streak = out(f, 2, 22);
  return (
    <SceneLife background={C.deep}>
      <Grid dark opacity={0.25 + move * 0.25} perspective />
      <FluidBlob x={1160} y={-330} size={980} color={C.sageDeep} opacity={0.22 + move * 0.12} />
      <Particles count={120} dark converge={out(f, 0, 25) * 0.88} opacity={(1 - ease(f, 24, 45)) * 0.9} />
      <Particles count={40} dark opacity={0.18 + move * 0.2} />

      <svg width="1920" height="1080" style={{position: 'absolute', inset: 0, opacity: 1 - move * 0.7}}>
        <defs><radialGradient id="opening-core"><stop offset="0" stopColor={C.sage} stopOpacity=".38" /><stop offset="45%" stopColor={C.accent} stopOpacity=".1" /><stop offset="100%" stopColor={C.deep} stopOpacity="0" /></radialGradient></defs>
        <circle cx={cx} cy={cy} r={440 + impact * 160} fill="url(#opening-core)" opacity={reveal} />
        {new Array(16).fill(null).map((_, i) => {
          const a = i * Math.PI * 2 / 16 + f * 0.004;
          const inner = 245 + (i % 3) * 22;
          const outer = 780 + (i % 4) * 90;
          return <line key={i} x1={960 + Math.cos(a) * outer} y1={450 + Math.sin(a) * outer} x2={960 + Math.cos(a) * inner} y2={450 + Math.sin(a) * inner} stroke={i % 4 === 0 ? C.accentLight : C.sage} strokeWidth={i % 4 === 0 ? 3 : 1} strokeDasharray="180 900" strokeDashoffset={180 + streak * 620} opacity={streak * (1 - impact * .55) * .55} />;
        })}
        {[0, 1, 2].map((i) => <circle key={i} cx={cx} cy={cy} r={size * .68 + i * 44} fill="none" stroke={i === 1 ? C.accentLight : C.sage} strokeWidth={i === 1 ? 2 : 1} strokeDasharray={i === 0 ? '110 28 8 34' : i === 1 ? '280 90' : '3 18'} opacity={reveal * (.5 - i * .1)} transform={`rotate(${f * (i % 2 ? -.8 : .55) + i * 45} ${cx} ${cy})`} />)}
        <circle cx="960" cy="433" r={50 + impact * 540} fill="none" stroke={C.sage} strokeWidth={3 - impact * 2} opacity={(1 - impact) * .7} />
        <circle cx="960" cy="433" r={30 + out(f, 27, 48) * 400} fill="none" stroke={C.accentLight} strokeWidth="2" opacity={(1 - out(f, 27, 48)) * .55} />
      </svg>

      <div style={{...abs(84, 62), opacity: .4 + reveal * .6}}><Eyebrow dark>DudLedger · Product film</Eyebrow></div>
      <div style={{...abs(1740, 70), fontFamily: MONO, fontSize: 18, letterSpacing: '.16em', color: C.sage, opacity: .55}}>00 / 04S</div>

      <div style={{...abs(x, y), width: size, height: size, perspective: 1300, zIndex: 12}}>
        {[0, 1].map((i) => {
          const ghost = (1 - out(f, 24, 40)) * reveal;
          const direction = i === 0 ? -1 : 1;
          return <div key={i} style={{position: 'absolute', inset: 0, borderRadius: size * .2, overflow: 'hidden', opacity: ghost * .22, transform: `translate(${direction * Math.sin(f * 1.7) * 28 * ghost}px, ${direction * 8 * ghost}px)`, filter: i === 0 ? 'sepia(1) saturate(2)' : 'hue-rotate(70deg)'}}><Img src={staticFile('icon.png')} style={{width: '100%', height: '100%', objectFit: 'cover'}} /></div>;
        })}
        <div style={{position: 'absolute', inset: 0, borderRadius: size * .2, overflow: 'hidden', background: C.ivory, border: '1px solid rgba(255,255,255,.72)', clipPath: `inset(${(1 - reveal) * 48}% ${(1 - reveal) * 48}% ${(1 - reveal) * 48}% ${(1 - reveal) * 48}% round ${size * .2}px)`, opacity: reveal, transform: `rotateY(${(1 - reveal) * 155 - move * 10}deg) rotateX(${(1 - reveal) * 48 + Math.sin(f / 15) * 2}deg) rotateZ(${(1 - reveal) * -18 - move * 5}deg) scale(${.25 + reveal * .75 + (1 - impact) * .06})`, boxShadow: `0 0 ${90 + impact * 70}px rgba(199,211,195,.24), 0 ${35 + move * 20}px ${90 + move * 40}px rgba(0,0,0,.42), inset 0 0 0 2px rgba(255,255,255,.45)`}}>
          <Img src={staticFile('icon.png')} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
          <div style={{position: 'absolute', top: '-35%', left: `${-90 + out(f, 14, 39) * 250}%`, width: '42%', height: '170%', background: 'linear-gradient(90deg, transparent, rgba(255,255,255,.9), transparent)', transform: 'skewX(-20deg)', mixBlendMode: 'screen', opacity: .8}} />
          <div style={{position: 'absolute', inset: 0, borderRadius: size * .2, border: `2px solid ${move > .5 ? C.accentLight : C.sage}`, opacity: .28 + Math.sin(f / 7) * .08}} />
        </div>
        <div style={{position: 'absolute', left: '8%', right: '8%', bottom: -32, height: 26, borderRadius: '50%', background: C.sage, filter: 'blur(18px)', opacity: reveal * .22}} />
      </div>

      <div style={{...abs(0, 690), width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', opacity: 1 - move, transform: `translateY(${move * 35}px)`, zIndex: 9}}>
        <KineticText text="DudLedger" latin size={70} color={C.ivory} delay={23} stagger={1.5} />
        <FadeUp delay={34} style={{marginTop: 18, fontFamily: MONO, fontSize: 17, letterSpacing: '.32em', color: C.sage}}>LEGAL DUE DILIGENCE TOOL</FadeUp>
      </div>

      <div style={{...abs(1330, 30), fontFamily: LATIN, fontSize: 650, fontWeight: 700, lineHeight: 1, color: 'transparent', WebkitTextStroke: `2px ${C.sage}`, opacity: question * .11, transform: `translateY(${70 - question * 70}px) rotate(${8 - question * 8}deg)`}}>?</div>
      <div style={{...abs(560, 252), opacity: move, zIndex: 8}}>
        <KineticText text="账簿很多。" size={128} color={C.ivory} delay={58} />
        <div style={{height: 24}} />
        <KineticText text="线索在哪里？" size={128} color={C.accentLight} delay={70} />
      </div>
      <FadeUp delay={88} style={{...abs(566, 760), opacity: move}}>
        <div style={{display: 'flex', alignItems: 'center', gap: 24, color: C.sage, fontSize: 23}}>
          <span style={{fontFamily: MONO, letterSpacing: '.14em'}}>EXCEL / CSV / XLS</span>
          <span style={{width: 110, height: 1, background: C.sage, opacity: .5}} />
          <span>从数字的噪声里，找到值得追问的一笔。</span>
        </div>
      </FadeUp>
      <div style={{position: 'absolute', inset: 0, background: C.ivory, opacity: flash * reveal * .16, pointerEvents: 'none', zIndex: 30}} />
      <div style={{...abs(0, 1030), width: '100%', height: 4, background: 'rgba(255,255,255,.06)', zIndex: 35}}>
        <div style={{height: '100%', width: `${interpolate(f, [0, 120], [0, 100], clamp)}%`, background: C.accentLight}} />
      </div>
    </SceneLife>
  );
};

const SheetCard: React.FC<{f: number; index: number; title: string; ext: string; x: number; y: number; rotate: number}> = ({f, index, title, ext, x, y, rotate}) => {
  const enter = pop(f, index * 6);
  const merge = ease(f, 38 + index * 3, 72 + index * 2);
  const tx = x + (1190 - x) * merge;
  const ty = y + (440 - y) * merge;
  return (
    <div style={{...abs(0, 0), width: 360, height: 450, padding: 28, borderRadius: 18, background: C.paper, border: `1px solid ${C.line}`, boxShadow: '0 35px 80px rgba(41,36,29,.16)', opacity: enter * (1 - ease(f, 68, 82)), transform: `translate(${tx}px, ${ty}px) rotate(${rotate * (1 - merge)}deg) scale(${enter * (1 - merge * .65)})`, transformOrigin: '50% 50%'}}>
      <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}><Icon name="table" size={38} color={C.accent} /><span style={{fontFamily: MONO, fontSize: 20, color: C.muted}}>{ext}</span></div>
      <div style={{marginTop: 26, fontSize: 28, fontWeight: 650}}>{title}</div>
      <div style={{marginTop: 26, display: 'grid', gridTemplateColumns: '1.1fr .8fr .9fr', gap: 0}}>
        {new Array(30).fill(null).map((_, i) => <div key={i} style={{height: 33, border: `1px solid ${C.line}`, background: i === 7 + index * 3 ? C.accentSoft : 'transparent', opacity: .5 + (i % 4) * .1}} />)}
      </div>
    </div>
  );
};

const Database: React.FC<{f: number}> = ({f}) => {
  const p = pop(f, 40);
  return (
    <div style={{...abs(1060, 310), width: 280, height: 300, opacity: p * (1 - ease(f, 69, 82)), transform: `scale(${p}) rotate(${-6 + Math.sin(f / 18) * 2}deg)`, filter: 'drop-shadow(0 32px 35px rgba(52,47,39,.22))'}}>
      {[0, 1, 2].map((i) => <div key={i} style={{position: 'absolute', top: i * 62, width: 280, height: 130, borderRadius: '50%', background: `linear-gradient(145deg, ${i === 0 ? '#FCFBF8' : '#D9D6CD'}, #B7C7B8)`, border: `2px solid ${C.sageDeep}`, transform: `translateZ(${i * 10}px)`}} />)}
      <div style={{...abs(0, 38), width: 280, textAlign: 'center', color: C.deep, fontFamily: MONO, fontSize: 22, fontWeight: 700, letterSpacing: '.08em'}}>SQLITE</div>
      <div style={{...abs(0, 76), width: 280, textAlign: 'center', color: C.soft, fontSize: 21}}>本地账套</div>
    </div>
  );
};

export const SceneImport: React.FC = () => {
  const f = useCurrentFrame();
  const reveal = out(f, 63, 90);
  return (
    <SceneLife background={C.ivory}>
      <Grid opacity={0.25} />
      <FluidBlob x={-360} y={560} size={720} color={C.accentSoft} opacity={0.62} />
      <div style={abs(86, 76)}><Eyebrow>01 · Intelligent import</Eyebrow></div>
      <div style={{...abs(82, 186), zIndex: 8}}>
        <KineticText text="复杂表格，" size={88} delay={5} />
        <KineticText text="一个账套。" size={104} color={C.accent} delay={18} />
        <FadeUp delay={44} style={{marginTop: 34, width: 520, color: C.soft, fontSize: 28, lineHeight: 1.65}}>AI 辅助识别表头与列含义<br />本地预检，再写入 SQLite。</FadeUp>
        <FadeUp delay={56} style={{marginTop: 34, display: 'flex', gap: 12, flexWrap: 'wrap', width: 550}}>
          <Chip icon="spark">有限样本识别</Chip><Chip icon="shield">整表本地处理</Chip>
        </FadeUp>
      </div>
      <div style={{...abs(0, 0), width: '100%', height: '100%', perspective: 1300}}>
        <SheetCard f={f} index={0} title="明细账" ext=".XLSX" x={1120} y={210} rotate={-10} />
        <SheetCard f={f} index={1} title="序时账" ext=".CSV" x={1450} y={390} rotate={9} />
        <SheetCard f={f} index={2} title="财务报表" ext=".XLS" x={1060} y={520} rotate={-3} />
        <Database f={f} />
        <Screen src="01_accounts.png" width={1080} label="DUDLEDGER / LOCAL LEDGER" style={{...abs(760, 224), opacity: reveal, clipPath: `inset(0 ${(1 - reveal) * 100}% 0 0 round 20px)`, transform: `translateZ(${reveal * 80}px) rotateY(${-12 + reveal * 9}deg) rotateX(${4 - reveal * 3}deg) scale(${.84 + reveal * .16})`, transformOrigin: '60% 50%'}} />
      </div>
      <div style={{...abs(84, 1000), color: C.muted, fontSize: 19}}>原始 Excel / CSV 不会被修改 · 表头结构可保存为本地模板</div>
    </SceneLife>
  );
};

export const SceneVoucher: React.FC = () => {
  const f = useCurrentFrame();
  const focus = out(f, 18, 42);
  const open = out(f, 48, 74);
  const cursorX = interpolate(f, [12, 45], [1550, 1300], clamp);
  const cursorY = interpolate(f, [12, 45], [660, 465], clamp);
  return (
    <SceneLife background={C.canvas}>
      <FluidBlob x={-420} y={-330} size={780} color={C.sage} opacity={0.55} />
      <div style={abs(82, 70)}><Eyebrow>02 · Follow the transaction</Eyebrow></div>
      <div style={{...abs(82, 188), zIndex: 12, width: 510}}>
        <KineticText text="从一笔交易，" size={76} delay={4} />
        <KineticText text="看全整张凭证。" size={76} color={C.accent} delay={16} />
        <FadeUp delay={38} style={{marginTop: 32, fontSize: 27, lineHeight: 1.65, color: C.soft}}>按科目、日期、摘要与往来单位筛选。<br />双击明细，集中查看已导入分录。</FadeUp>
        <FadeUp delay={55} style={{marginTop: 34}}><Chip icon="voucher">不再跨表拼凑凭证</Chip></FadeUp>
      </div>
      <div style={{...abs(610, 166), perspective: 1400}}>
        <Screen src="02_entries.png" width={1210} label="DUDLEDGER / JOURNAL" style={{transform: `rotateY(${-10 + focus * 6}deg) rotateX(${3 - focus * 2}deg) scale(${.96 + focus * .04})`, filter: `brightness(${1 - open * .3}) blur(${open * 2}px)`, transformOrigin: '60% 50%'}} />
        <div style={{...abs(300, 276), width: 690, height: 62, border: `3px solid ${C.accent}`, borderRadius: 8, background: C.accentSoft, opacity: .25 + focus * .45, boxShadow: `0 0 0 ${6 + Math.sin(f / 4) * 3}px rgba(166,80,54,.12)`, transform: `translateX(${(1 - focus) * 90}px)`}} />
        <Cursor x={cursorX - 610} y={cursorY - 166} pressed={f > 40 && f < 50} />
        <Screen src="04_voucher.png" width={920} height={330} label="DUDLEDGER / COMPLETE VOUCHER" chrome={false} imageStyle={{objectFit: 'cover', objectPosition: 'top'}} style={{...abs(170, 350), opacity: open, clipPath: `inset(${(1 - open) * 48}% ${(1 - open) * 50}% ${(1 - open) * 48}% ${(1 - open) * 50}% round 20px)`, transform: `translateY(${(1 - open) * 130}px) translateZ(${open * 170}px) rotateY(${10 - open * 10}deg)`, boxShadow: '0 60px 140px rgba(32,39,37,.32)'}} />
      </div>
      <FadeUp delay={76} style={{...abs(1040, 942), zIndex: 20, display: 'flex', alignItems: 'center', gap: 18, color: C.soft, fontSize: 23}}><span style={{width: 46, height: 2, background: C.accent}} />借方与贷方，回到同一张凭证。</FadeUp>
    </SceneLife>
  );
};

export const SceneCalculate: React.FC = () => {
  const f = useCurrentFrame();
  const select = out(f, 18, 48);
  const second = out(f, 44, 62);
  return (
    <SceneLife background="#E5E9E0">
      <Grid opacity={0.25} />
      <FluidBlob x={1280} y={-280} size={760} color={C.paper} opacity={0.65} />
      <div style={abs(84, 72)}><Eyebrow>03 · Calculate in context</Eyebrow></div>
      <div style={{...abs(82, 186), zIndex: 8}}>
        <KineticText text="框选，" size={116} delay={4} />
        <KineticText text="即合计。" size={116} color={C.accent} delay={15} />
        <FadeUp delay={35} style={{marginTop: 30, color: C.soft, fontSize: 27, lineHeight: 1.6}}>账表与凭证内直接框选。<br />合计、计数、金额项数同步呈现。</FadeUp>
      </div>
      <Screen src="01_accounts.png" width={1180} label="DUDLEDGER / MULTI-SELECTION" style={{...abs(670, 172), transform: `perspective(1500px) rotateY(${-7 + Math.sin(f / 40) * 2}deg) rotateX(2deg)`}} />
      <div style={{...abs(1190, 355), width: 175, height: 55, border: `3px solid ${C.accent}`, background: 'rgba(166,80,54,.13)', borderRadius: 5, opacity: select, transform: `scaleX(${select})`, transformOrigin: 'left'}} />
      <div style={{...abs(1190, 414), width: 175, height: 55, border: `3px solid ${C.sageDeep}`, background: 'rgba(94,120,103,.15)', borderRadius: 5, opacity: second, transform: `scaleX(${second})`, transformOrigin: 'left'}} />
      <Cursor x={1230 + select * 110} y={355 + select * 80} pressed={f > 20 && f < 50} />
      <FadeUp delay={43} style={{...abs(88, 562), width: 520, padding: '30px 34px', background: 'rgba(255,255,255,.83)', border: `1px solid ${C.line}`, borderRadius: 20, boxShadow: '0 24px 70px rgba(48,48,44,.13)', backdropFilter: 'blur(16px)'}}>
        <div style={{display: 'flex', justifyContent: 'space-between', color: C.muted, fontSize: 21}}><span>跨选区计算</span><span style={{fontFamily: MONO}}>2 RANGES</span></div>
        <div style={{marginTop: 24, fontFamily: MONO, fontSize: 25, color: C.soft}}>6,558,800.00<br /><span style={{color: C.accent}}>+ 520,000.00</span></div>
        <div style={{height: 1, background: C.line, margin: '22px 0'}} />
        <CountUp value={7078800} start={45} end={72} style={{fontSize: 50, fontWeight: 700, letterSpacing: '-.04em', color: C.ink}} />
      </FadeUp>
      <FadeUp delay={65} style={{...abs(86, 930), display: 'flex', gap: 12}}><Chip icon="calc">多选区加减</Chip><Chip>重叠只计一次</Chip><Chip>保留计算来源</Chip></FadeUp>
    </SceneLife>
  );
};

export const SceneIssues: React.FC = () => {
  const f = useCurrentFrame();
  const shift = ease(f, 42, 66);
  return (
    <SceneLife background={C.ivory}>
      <FluidBlob x={-390} y={500} size={720} color={C.accentSoft} opacity={0.7} />
      <div style={abs(84, 70)}><Eyebrow>04 · Build the issue trail</Eyebrow></div>
      <div style={{...abs(82, 176), width: 520, zIndex: 12}}>
        <KineticText text="把疑点，" size={96} delay={4} />
        <KineticText text="变成问题。" size={88} color={C.accent} delay={16} />
        <FadeUp delay={34} style={{marginTop: 30, fontSize: 27, lineHeight: 1.65, color: C.soft}}>关注凭证、填写备注，<br />再按尽调问题归集与核查。</FadeUp>
      </div>
      <div style={{...abs(585, 172), perspective: 1400}}>
        <Screen src="05_marks.png" width={1240} label="DUDLEDGER / FOLLOW-UP" style={{opacity: 1 - shift, transform: `translateX(${-shift * 90}px) rotateY(-7deg) scale(${1 - shift * .05})`}} />
        <Screen src="06_issue_workbench.png" width={1240} label="DUDLEDGER / ISSUE WORKBENCH" style={{...abs(0, 0), opacity: shift, clipPath: `inset(0 0 0 ${(1 - shift) * 100}% round 20px)`, transform: `translateX(${(1 - shift) * 100}px) rotateY(${-10 + shift * 5}deg)`}} />
      </div>
      <svg width="700" height="520" viewBox="0 0 700 520" style={{...abs(80, 490), zIndex: 15, overflow: 'visible'}}>
        {[0, 1, 2].map((i) => {
          const draw = out(f, 42 + i * 5, 72 + i * 5);
          return <path key={i} d={`M${440 + i * 20} ${32 + i * 48} C300 ${40 + i * 70} 290 210 165 258`} fill="none" stroke={i === 1 ? C.accent : C.sageDeep} strokeWidth="2" strokeDasharray="600" strokeDashoffset={600 * (1 - draw)} opacity={.7} />;
        })}
      </svg>
      <FadeUp delay={50} style={{...abs(84, 684), zIndex: 16, width: 390, padding: 28, borderRadius: 18, background: C.deep, color: C.ivory, boxShadow: '0 28px 70px rgba(32,39,37,.25)'}}>
        <div style={{display: 'flex', alignItems: 'center', gap: 14}}><Icon name="issue" size={34} color={C.accentLight} /><span style={{fontSize: 28, fontWeight: 650}}>关联方资金往来</span></div>
        <div style={{marginTop: 18, color: C.sage, fontSize: 21, lineHeight: 1.65}}>3 张相关凭证<br />状态：待核查 · 笔记自动保存</div>
      </FadeUp>
      <FadeUp delay={70} style={{...abs(88, 938), display: 'flex', gap: 12}}><Chip>一张凭证，多个问题</Chip><Chip>归集快照</Chip><Chip>复核状态</Chip></FadeUp>
    </SceneLife>
  );
};

const AI_NODES = ['其他应收款', '其他应付款', '实收资本', '资本公积', '营业外支出', '财务费用'];

export const SceneAI: React.FC = () => {
  const f = useCurrentFrame();
  const network = out(f, 16, 68);
  return (
    <SceneLife background={C.deep}>
      <Grid dark opacity={0.3} />
      <FluidBlob x={980} y={20} size={980} color={C.sageDeep} opacity={0.26} />
      <Particles count={84} dark opacity={0.72} />
      <div style={{...abs(760, -160), fontFamily: LATIN, fontSize: 650, fontWeight: 700, color: 'transparent', WebkitTextStroke: `2px ${C.sage}`, opacity: .07, transform: `translateX(${(1 - network) * 120}px)`}}>AI</div>
      <div style={abs(84, 72)}><Eyebrow dark>05 · Assisted review</Eyebrow></div>
      <div style={{...abs(82, 204), zIndex: 8}}>
        <KineticText text="AI 初步抽查，" size={88} color={C.ivory} delay={4} />
        <KineticText text="先找到线索。" size={88} color={C.accentLight} delay={17} />
        <FadeUp delay={40} style={{marginTop: 34, width: 570, color: C.sage, fontSize: 27, lineHeight: 1.7}}>按专项问题筛选值得进一步核查的凭证。<br />附带同张凭证分录与核查建议。</FadeUp>
      </div>
      <div style={{...abs(770, 110), width: 1050, height: 750, perspective: 1200}}>
        <svg width="1050" height="750" viewBox="0 0 1050 750" style={{position: 'absolute', inset: 0}}>
          <defs><radialGradient id="core-glow"><stop offset="0" stopColor={C.accentLight} stopOpacity=".8" /><stop offset="1" stopColor={C.accent} stopOpacity="0" /></radialGradient></defs>
          <circle cx="565" cy="365" r={180 + Math.sin(f / 12) * 8} fill="url(#core-glow)" opacity={.35} />
          {AI_NODES.map((_, i) => {
            const angle = (-145 + i * 58) * Math.PI / 180;
            const x = 565 + Math.cos(angle) * 390;
            const y = 365 + Math.sin(angle) * 280;
            const draw = out(f, 20 + i * 4, 52 + i * 4);
            return <path key={i} d={`M565 365 Q${(565 + x) / 2 + Math.sin(f / 20 + i) * 35} ${(365 + y) / 2 - 35} ${x} ${y}`} fill="none" stroke={i % 2 ? C.sage : C.accentLight} strokeWidth="2" strokeDasharray="600" strokeDashoffset={(1 - draw) * 600} opacity={.6} />;
          })}
        </svg>
        <div style={{...abs(425, 255), width: 280, height: 220, borderRadius: '50%', background: 'rgba(250,249,245,.96)', border: `1px solid ${C.sage}`, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: C.deep, boxShadow: `0 0 90px rgba(199,211,195,.18)`, transform: `scale(${network}) rotateY(${(1 - network) * 90}deg)`}}>
          <Icon name="spark" size={48} color={C.accent} />
          <div style={{fontFamily: LATIN, fontSize: 54, fontWeight: 700, marginTop: 8}}>30</div>
          <div style={{fontSize: 22, color: C.soft}}>项专项问题</div>
        </div>
        {AI_NODES.map((label, i) => {
          const angle = (-145 + i * 58) * Math.PI / 180;
          const x = 565 + Math.cos(angle) * 390;
          const y = 365 + Math.sin(angle) * 280;
          const p = pop(f, 28 + i * 4);
          return <div key={label} style={{...abs(x - 94, y - 30), width: 188, padding: '15px 8px', textAlign: 'center', borderRadius: 999, background: 'rgba(255,255,255,.08)', border: '1px solid rgba(255,255,255,.2)', color: C.ivory, fontSize: 21, backdropFilter: 'blur(10px)', opacity: p, transform: `scale(${p})`}}>{label}</div>;
        })}
      </div>
      <FadeUp delay={62} style={{...abs(86, 770), zIndex: 10, display: 'flex', flexDirection: 'column', gap: 18}}>
        <div style={{fontSize: 32, color: C.ivory, fontWeight: 600}}>模型提供线索，律师作出判断。</div>
        <div style={{display: 'flex', gap: 12}}><Chip dark icon="shield">发送前预览确认</Chip><Chip dark>按需启用</Chip></div>
      </FadeUp>
      <div style={{...abs(86, 1000), color: C.sage, opacity: .65, fontSize: 19}}>AI 抽查结果用于初步筛选，不替代合同、流水与原始单据核查。</div>
    </SceneLife>
  );
};

const LocalObject: React.FC<{f: number}> = ({f}) => {
  const enter = pop(f, 8);
  return (
    <div style={{...abs(990, 180), width: 700, height: 700, perspective: 1100}}>
      <svg width="700" height="700" style={{position: 'absolute', inset: 0, transform: `rotate(${f * .25}deg)`}}>
        <circle cx="350" cy="350" r="285" fill="none" stroke={C.sageDeep} strokeWidth="1" strokeDasharray="4 14" opacity={.5} />
        <circle cx="350" cy="350" r="240" fill="none" stroke={C.accent} strokeWidth="2" strokeDasharray="160 40 20 60" opacity={.35} />
      </svg>
      <div style={{...abs(160, 160), width: 380, height: 380, transformStyle: 'preserve-3d', transform: `rotateX(${58 + Math.sin(f / 35) * 4}deg) rotateZ(${-32 + f * .12}deg) scale(${enter})`}}>
        {[0, 1, 2, 3].map((i) => <div key={i} style={{position: 'absolute', inset: 0, borderRadius: 35, background: i === 3 ? C.deep : i === 2 ? C.sage : i === 1 ? C.accentSoft : C.paper, border: `2px solid ${i === 3 ? C.deep : C.line}`, boxShadow: '0 30px 60px rgba(32,39,37,.14)', transform: `translateZ(${i * 52}px)`, opacity: .96}} />)}
        <div style={{position: 'absolute', inset: 0, borderRadius: 35, background: C.deep, display: 'flex', alignItems: 'center', justifyContent: 'center', transform: 'translateZ(160px) rotateZ(32deg) rotateX(-58deg)', transformOrigin: '50% 50%'}}><Icon name="shield" size={150} color={C.sage} strokeWidth={1.2} /></div>
      </div>
      <div style={{...abs(190, 590), width: 320, textAlign: 'center', fontFamily: MONO, color: C.soft, fontSize: 20, letterSpacing: '.18em'}}>LOCAL BY DEFAULT</div>
    </div>
  );
};

export const SceneLocal: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <SceneLife background={C.canvas}>
      <FluidBlob x={780} y={-120} size={1120} color={C.sage} opacity={0.72} />
      <FluidBlob x={-400} y={550} size={700} color={C.accentSoft} opacity={0.55} />
      <Particles count={36} opacity={0.35} converge={ease(f, 0, 90) * .25} />
      <Grid opacity={0.15} />
      <div style={abs(84, 72)}><Eyebrow>06 · Your data, your workspace</Eyebrow></div>
      <div style={{...abs(84, 196), zIndex: 8}}>
        <KineticText text="默认本地处理。" size={102} delay={4} />
        <FadeUp delay={30} style={{marginTop: 34, width: 720, color: C.soft, fontSize: 29, lineHeight: 1.65}}>查询、筛选、计算与备注，<br />都在你的本机账套中完成。</FadeUp>
      </div>
      <LocalObject f={f} />
      <div style={{...abs(88, 585), display: 'flex', flexDirection: 'column', gap: 16, alignItems: 'flex-start'}}>
        <FadeUp delay={38}><Chip icon="table">原始表格不被修改</Chip></FadeUp>
        <FadeUp delay={47}><Chip icon="search">来源与原行号可追溯</Chip></FadeUp>
        <FadeUp delay={56}><Chip icon="spark">云端 AI 按需启用</Chip></FadeUp>
      </div>
      <FadeUp delay={72} style={{...abs(88, 944), color: C.muted, fontSize: 21}}>本地 SQLite 账套 · 金额以整数分存储 · 十进制计算</FadeUp>
    </SceneLife>
  );
};

export const SceneFinale: React.FC = () => {
  const f = useCurrentFrame();
  const logo = pop(f, 3);
  const converge = ease(f, 0, 42) * .8;
  const particleFade = 1 - ease(f, 34, 62);
  return (
    <SceneLife background={C.ivory}>
      <Grid opacity={0.2} />
      <FluidBlob x={-260} y={30} size={850} color={C.sage} opacity={0.7} />
      <FluidBlob x={1370} y={-390} size={750} color={C.accentSoft} opacity={0.65} />
      <Particles count={94} converge={converge} opacity={particleFade * .65} />
      <div style={abs(86, 66)}><Eyebrow>DudLedger · Product film</Eyebrow></div>
      <div style={{...abs(190, 276), width: 310, height: 310, borderRadius: 62, overflow: 'hidden', boxShadow: '0 40px 100px rgba(48,48,44,.2)', opacity: logo, clipPath: `inset(${(1 - logo) * 50}% ${(1 - logo) * 50}% ${(1 - logo) * 50}% ${(1 - logo) * 50}% round 62px)`, transform: `perspective(1000px) rotateY(${(1 - logo) * 75}deg) rotateZ(${(1 - logo) * -12}deg) scale(${.7 + logo * .3})`}}>
        <Img src={staticFile('icon.png')} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
      </div>
      <div style={{...abs(590, 292), zIndex: 8}}>
        <KineticText text="DudLedger" latin size={158} delay={12} stagger={2.2} />
        <FadeUp delay={36} style={{marginTop: 16, fontSize: 48, fontWeight: 550, letterSpacing: '.08em', color: C.soft}}>律师尽调查账工具</FadeUp>
      </div>
      <div style={{...abs(194, 714), zIndex: 8}}>
        <KineticText text="让每一笔交易，都有迹可循。" size={64} color={C.ink} delay={36} stagger={0.8} />
      </div>
      <FadeUp delay={54} style={{...abs(198, 850), display: 'flex', alignItems: 'center', gap: 18, fontFamily: MONO, fontSize: 21, color: C.soft, letterSpacing: '.08em'}}>
        {['EXCEL', 'LEDGER', 'VOUCHER', 'ISSUE'].map((item, i) => <React.Fragment key={item}><span style={{color: i === 3 ? C.accent : C.soft}}>{item}</span>{i < 3 && <span style={{width: 58, height: 1, background: C.line}} />}</React.Fragment>)}
      </FadeUp>
      <FadeUp delay={64} style={{...abs(198, 944), display: 'flex', gap: 14}}><Chip icon="database">本地账套</Chip><Chip icon="voucher">凭证穿透</Chip><Chip icon="issue">问题归集</Chip><Chip icon="spark">AI 辅助</Chip></FadeUp>
    </SceneLife>
  );
};

export const TransitionWipe: React.FC<{index: number}> = ({index}) => {
  const f = useCurrentFrame();
  const p = f / 17;
  const x = interpolate(p, [0, 1], [-2700, 2350], clamp);
  const color = index % 3 === 0 ? C.accent : index % 3 === 1 ? C.sageDeep : C.deep;
  return (
    <div style={{position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 100, overflow: 'hidden'}}>
      <div style={{position: 'absolute', left: x, top: -220, width: 2200, height: 1550, background: `linear-gradient(110deg, transparent 0%, ${color} 16%, ${color} 78%, transparent 100%)`, transform: 'skewX(-18deg)', opacity: Math.sin(p * Math.PI) * .96}} />
      <div style={{position: 'absolute', left: x + 2050, top: -200, width: 10, height: 1500, background: C.paper, transform: 'skewX(-18deg)', opacity: Math.sin(p * Math.PI)}} />
    </div>
  );
};
