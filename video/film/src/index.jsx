import React from "react";
import {
  AbsoluteFill, Audio, Composition, Easing, Img, OffthreadVideo, Sequence,
  interpolate, registerRoot, spring, staticFile, useCurrentFrame, useVideoConfig,
} from "remotion";
import narration from "../narration.json";

const FPS = 30;
const S = (seconds) => Math.round(seconds * FPS);
const INK = "#0a0f1c";
const MUTED = "#5b6678";
const BLUE = "#2f6bff";
const GREEN = "#0a9968";
const SANS = '"Inter Display", Inter, "DejaVu Sans", sans-serif';
const TEXT = 'Inter, "DejaVu Sans", sans-serif';
const MONO = '"DejaVu Sans Mono", monospace';
const out = Easing.bezier(0.16, 1, 0.3, 1);
const inOut = Easing.bezier(0.65, 0, 0.35, 1);
const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" };
const ramp = (frame, from, to, a = 0, b = 1, easing = out) =>
  interpolate(frame, [from, to], [a, b], { ...clamp, easing });

/* ── the stage: white, with slow light and fine drifting lines ────────────── */
const Stage = () => {
  const frame = useCurrentFrame();
  const drift = Math.sin(frame / 240) * 60;
  const lines = Array.from({ length: 7 }, (_, i) => {
    const t = frame / 110 + i * 0.9;
    const y = 180 + i * 125 + Math.sin(t) * 46;
    return <path key={i} fill="none" stroke={BLUE} strokeWidth={1.5} strokeOpacity={0.045 + (i % 3) * 0.02}
      d={`M -80 ${y} C 480 ${y - 150 + Math.cos(t * 0.8) * 110}, 1380 ${y + 170 + Math.sin(t * 1.1) * 120}, 2000 ${y - 30 + Math.cos(t) * 60}`} />;
  });
  return (
    <AbsoluteFill style={{ background: "#fbfcfe" }}>
      <AbsoluteFill style={{ background: `radial-gradient(900px 560px at ${50 + drift / 40}% 32%, #ffffff 0%, rgba(255,255,255,0) 70%), radial-gradient(1100px 700px at 82% 96%, rgba(47,107,255,.07) 0%, rgba(47,107,255,0) 70%), radial-gradient(900px 600px at 8% 4%, rgba(47,107,255,.05) 0%, rgba(47,107,255,0) 70%)` }} />
      <svg width={1920} height={1080}>{lines}</svg>
    </AbsoluteFill>
  );
};

/* ── the mark: one shape that never settles on a single form ──────────────── */
const superellipse = (n, r, steps = 96) => {
  let d = "";
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * Math.PI * 2;
    const c = Math.cos(a), s = Math.sin(a);
    const x = Math.sign(c) * Math.pow(Math.abs(c), 2 / n) * r;
    const y = Math.sign(s) * Math.pow(Math.abs(s), 2 / n) * r;
    d += `${i ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`;
  }
  return d + "Z";
};

const Glyph = ({ size = 120, t = 0, draw = 1 }) => {
  const n1 = 2 + (Math.sin(t / 26) * 0.5 + 0.5) * 3.2;         // circle ⇄ squircle
  const n2 = 2 + (Math.cos(t / 26) * 0.5 + 0.5) * 3.2;         // the inner one runs opposite
  const r = size / 2 - 7;
  const length = 2 * Math.PI * r * 1.2;
  return (
    <svg width={size} height={size} viewBox={`${-size / 2} ${-size / 2} ${size} ${size}`} style={{ overflow: "visible" }}>
      <path d={superellipse(n1, r)} fill="none" stroke={INK} strokeWidth={size * 0.085} strokeLinejoin="round"
        strokeDasharray={length} strokeDashoffset={(1 - draw) * length} transform={`rotate(${t * 0.35})`} />
      <path d={superellipse(n2, r * 0.42)} fill={BLUE} transform={`rotate(${-t * 0.5}) scale(${draw})`} />
    </svg>
  );
};

const Logo = ({ at = 0, size = 150, sub }) => {
  const frame = useCurrentFrame();
  const local = frame - at;
  const draw = ramp(local, 0, 34, 0, 1, inOut);
  const letters = "morph".split("");
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: size * 0.2 }}>
      <div style={{ display: "flex", alignItems: "center", gap: size * 0.24 }}>
        <Glyph size={size * 0.92} t={Math.max(0, local)} draw={draw} />
        <div style={{ display: "flex", fontFamily: SANS, fontWeight: 700, fontSize: size, letterSpacing: -size * 0.045, color: INK, lineHeight: 1 }}>
          {letters.map((ch, i) => {
            const p = ramp(local, 16 + i * 4, 40 + i * 4);
            return <span key={i} style={{ display: "inline-block", opacity: p,
              transform: `translateX(${(1 - p) * -34}px)`, filter: `blur(${(1 - p) * 10}px)` }}>{ch}</span>;
          })}
        </div>
      </div>
      {sub ? <div style={{ fontFamily: TEXT, fontSize: size * 0.2, color: MUTED, letterSpacing: 0.5,
        opacity: ramp(local, 44, 66) }}>{sub}</div> : null}
    </div>
  );
};

/* ── a scene: arrives with depth, leaves quietly ──────────────────────────── */
const Scene = ({ from, to, children }) => {
  const frame = useCurrentFrame();
  const a = S(from), b = S(to);
  if (frame < a - 1 || frame > b + 1) { return null; }
  const enter = ramp(frame, a, a + 20);
  const exit = ramp(frame, b - 10, b, 0, 1, Easing.in(Easing.cubic));
  return (
    <AbsoluteFill style={{ opacity: Math.min(enter, 1 - exit), perspective: 1800,
      filter: `blur(${(1 - enter) * 10 + exit * 6}px)` }}>
      <AbsoluteFill style={{ transform: `translateY(${(1 - enter) * 40 - exit * 18}px) rotateX(${(1 - enter) * 5}deg) scale(${0.965 + 0.035 * enter + exit * 0.012})` }}>
        <Sequence from={a} layout="none">{children}</Sequence>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/* ── a browser window around real footage ─────────────────────────────────── */
const Window = ({ url, children, zoom = 1, ox = 50, oy = 50, dark = false }) => {
  const width = 1360, inner = 765;
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-start", paddingTop: 100 }}>
      <div style={{ width, borderRadius: 20, overflow: "hidden", background: dark ? "#0d1014" : "#fff",
        boxShadow: "0 50px 110px rgba(10,15,28,.20), 0 8px 24px rgba(10,15,28,.08)", border: "1px solid rgba(10,15,28,.10)" }}>
        <div style={{ height: 42, display: "flex", alignItems: "center", gap: 8, padding: "0 16px",
          background: "#f3f5f9", borderBottom: "1px solid rgba(10,15,28,.07)" }}>
          {[0, 1, 2].map((i) => <div key={i} style={{ width: 11, height: 11, borderRadius: 6, background: "#dfe3ea" }} />)}
          <div style={{ margin: "0 auto", width: 620, height: 26, borderRadius: 13, background: "#fff",
            border: "1px solid rgba(10,15,28,.07)", display: "flex", alignItems: "center", justifyContent: "center",
            fontFamily: TEXT, fontSize: 14, color: MUTED }}>{url}</div>
          <div style={{ width: 49 }} />
        </div>
        <div style={{ width, height: inner, overflow: "hidden", position: "relative" }}>
          <div style={{ position: "absolute", inset: 0, transform: `scale(${zoom})`, transformOrigin: `${ox}% ${oy}%` }}>
            {children}
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};

const Clip = ({ start, rate = 1 }) => (
  <OffthreadVideo src={staticFile("dashboard.mp4")} startFrom={S(start)} playbackRate={rate} muted
    style={{ width: "100%", display: "block" }} />
);

/* a still from github.com, with optional rings drawn over the parts that matter */
const Still = ({ name, rings = [] }) => {
  const frame = useCurrentFrame();
  return (
    <div style={{ position: "relative", width: "100%" }}>
      <Img src={staticFile(name)} style={{ width: "100%", display: "block" }} />
      {rings.map((r, i) => {
        const p = ramp(frame, r.at, r.at + 14);
        return <div key={i} style={{ position: "absolute", left: `${r.x}%`, top: `${r.y}%`, width: `${r.w}%`, height: `${r.h}%`,
          border: `3px solid ${r.color || BLUE}`, borderRadius: 12, opacity: p,
          boxShadow: `0 0 0 ${(1 - p) * 26}px ${(r.color || BLUE)}22`, transform: `scale(${1.08 - 0.08 * p})` }} />;
      })}
    </div>
  );
};

const Tag = ({ children, tone = BLUE }) => (
  <div style={{ position: "absolute", top: 36, left: 0, right: 0, display: "flex", justifyContent: "center" }}>
    <div style={{ display: "flex", alignItems: "center", gap: 12, fontFamily: TEXT, fontSize: 19, fontWeight: 600,
      letterSpacing: 2.4, color: INK, background: "rgba(255,255,255,.9)", border: "1px solid rgba(10,15,28,.08)",
      borderRadius: 999, padding: "10px 22px 10px 16px", boxShadow: "0 8px 22px rgba(10,15,28,.06)" }}>
      <div style={{ width: 9, height: 9, borderRadius: 5, background: tone }} />{children}</div>
  </div>
);

/* ── a card that types real, abridged terminal output ─────────────────────── */
const Terminal = ({ lines, title }) => {
  const frame = useCurrentFrame();
  let budget = frame * 4.4;
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-start", paddingTop: 196 }}>
      <div style={{ width: 1300, borderRadius: 20, background: "#fff", border: "1px solid rgba(10,15,28,.10)",
        boxShadow: "0 50px 110px rgba(10,15,28,.16)", overflow: "hidden" }}>
        <div style={{ height: 42, background: "#f3f5f9", borderBottom: "1px solid rgba(10,15,28,.07)",
          display: "flex", alignItems: "center", padding: "0 18px", fontFamily: TEXT, fontSize: 15, color: MUTED }}>{title}</div>
        <div style={{ padding: "30px 40px 38px", fontFamily: MONO, fontSize: 25, lineHeight: 1.66, color: INK, minHeight: 370 }}>
          {lines.map((line, i) => {
            const text = line.text;
            const shown = line.instant ? (budget > 0 ? text : "") : text.slice(0, Math.max(0, Math.floor(budget)));
            budget -= line.instant ? 12 : text.length + 8;
            if (!shown) { return null; }
            return <div key={i} style={{ color: line.color || INK, fontWeight: line.bold ? 700 : 400, whiteSpace: "pre-wrap" }}>{shown}</div>;
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
};

/* ── intro ────────────────────────────────────────────────────────────────── */
const Word = ({ children, at, color = INK }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: frame - at, fps, config: { damping: 20, stiffness: 110, mass: 0.9 } });
  return <span style={{ display: "inline-block", marginRight: 30, color, opacity: p,
    transform: `translateY(${(1 - p) * 60}px)`, filter: `blur(${(1 - p) * 12}px)` }}>{children}</span>;
};

const Intro = () => {
  const frame = useCurrentFrame();
  const leave = ramp(frame, S(3.9), S(4.5), 0, 1, Easing.in(Easing.cubic));
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
      <div style={{ position: "absolute", opacity: 1 - leave, transform: `scale(${1 + leave * 0.08})`, filter: `blur(${leave * 14}px)`,
        fontFamily: SANS, fontWeight: 700, fontSize: 132, letterSpacing: -5, textAlign: "center", lineHeight: 1.08 }}>
        <div><Word at={S(0.6)}>AI</Word><Word at={S(0.85)}>can</Word><Word at={S(1.1)}>act.</Word></div>
        <div><Word at={S(1.9)} color={BLUE}>But</Word><Word at={S(2.1)} color={BLUE}>who</Word><Word at={S(2.3)} color={BLUE}>decides?</Word></div>
      </div>
      {frame > S(4.3) ? <Logo at={S(4.4)} size={170} sub="for Hermes Agent" /> : null}
    </AbsoluteFill>
  );
};

const Statement = () => {
  const frame = useCurrentFrame();
  const line = (text, at, color) => {
    const p = ramp(frame, at, at + 22);
    return <div style={{ color, opacity: p, transform: `translateY(${(1 - p) * 34}px)`, filter: `blur(${(1 - p) * 8}px)` }}>{text}</div>;
  };
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", paddingBottom: 70 }}>
      <div style={{ width: 1500, textAlign: "center", fontFamily: SANS, fontWeight: 700, fontSize: 76, lineHeight: 1.16, letterSpacing: -2.4 }}>
        {line("One adaptive workspace", 2, INK)}
        {line("across your applications.", 10, INK)}
        {line("You stay in control of every change.", 40, BLUE)}
      </div>
    </AbsoluteFill>
  );
};

const Outro = () => {
  const frame = useCurrentFrame();
  const p = ramp(frame, 48, 74);
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 44 }}>
      <Logo at={4} size={180} />
      <div style={{ fontFamily: SANS, fontWeight: 600, fontSize: 56, letterSpacing: -1.2, color: INK, opacity: p,
        transform: `translateY(${(1 - p) * 22}px)`, filter: `blur(${(1 - p) * 8}px)` }}>Software that adapts to you.</div>
      <div style={{ fontFamily: TEXT, fontSize: 27, color: MUTED, letterSpacing: 0.6, opacity: ramp(frame, 74, 98) }}>
        Powered by Hermes Agent  ·  github.com/Timioni001/morph-hermes</div>
    </AbsoluteFill>
  );
};

/* ── captions, in step with the narration ─────────────────────────────────── */
const Captions = () => {
  const frame = useCurrentFrame();
  const line = narration.find((n) => frame >= S(n.at) - 2 && frame <= S(n.at + n.dur) + 8);
  if (!line || line.id === "n1" || line.id === "n10") { return null; }
  const o = Math.min(ramp(frame, S(line.at) - 2, S(line.at) + 8), ramp(frame, S(line.at + line.dur), S(line.at + line.dur) + 8, 1, 0));
  const text = line.text.replace(/A\.I\./g, "AI").replace(/Open A\.P\.I\./g, "OpenAPI").replace(/A\.P\.I\./g, "API").replace(/Morph/g, "MORPH");
  return (
    <div style={{ position: "absolute", bottom: 30, left: 0, right: 0, display: "flex", justifyContent: "center", opacity: o }}>
      <div style={{ maxWidth: 1480, fontFamily: TEXT, fontSize: 30, fontWeight: 500, lineHeight: 1.3, color: INK, textAlign: "center",
        background: "rgba(255,255,255,.96)", border: "1px solid rgba(10,15,28,.07)", borderRadius: 18,
        padding: "13px 30px", boxShadow: "0 12px 34px rgba(10,15,28,.08)" }}>{text}</div>
    </div>
  );
};

const HERMES = "localhost:9119/morph  ·  Hermes dashboard";
const GH = "github.com/Timioni001/morph-demo/issues";

const Film = () => {
  const frame = useCurrentFrame();
  const z = (from, to, a, b) => ramp(frame, S(from), S(to), a, b, inOut);
  return (
    <AbsoluteFill style={{ background: "#fff" }}>
      <Stage />
      <Scene from={0} to={7.6}><Intro /></Scene>
      <Scene from={7.6} to={11.8}><Statement /></Scene>

      <Scene from={11.8} to={17.6}>
        <Tag>CONNECTED WITH ONE COMMAND · CAPABILITIES READ FROM THE API</Tag>
        <Window url={HERMES} dark zoom={z(11.8, 17.6, 1.0, 1.22)} ox={8} oy={10}><Clip start={5.6} /></Window>
      </Scene>

      <Scene from={17.6} to={21.6}>
        <Tag tone={INK}>A REAL GITHUB REPOSITORY</Tag>
        <Window url={GH} zoom={z(17.6, 21.6, 1.0, 1.14)} ox={30} oy={36}>
          <Still name="github-before-open.png" rings={[{ at: 34, x: 18.4, y: 32.4, w: 12.4, h: 4.6 }]} /></Window>
      </Scene>

      <Scene from={21.6} to={28.6}>
        <Tag>THE HERMES AGENT, WORKING THROUGH MORPH</Tag>
        <Terminal title="hermes chat  ·  real session, abridged" lines={[
          { text: '$ hermes chat -q "Triage the GitHub issues… propose, do not approve."', bold: true },
          { text: "⚡ morph_discover", color: MUTED, instant: true },
          { text: "⚡ morph_inspect_record  ×4", color: MUTED, instant: true },
          { text: "⚡ morph_propose_action  ×5", color: BLUE, instant: true },
          { text: "☤ All five proposals are submitted and pending approval:" },
          { text: "  close #3 and #6 as not planned — duplicates of #1" },
          { text: "  retitle #7 · label #7 bug · assign #1 to you" },
          { text: "  …only take effect once a person approves — I have no tool to approve.", color: GREEN },
        ]} />
      </Scene>

      <Scene from={28.6} to={31.4}>
        <Tag>STATE A GOAL</Tag>
        <Window url={HERMES} dark zoom={1.12} ox={50} oy={12}><Clip start={12.4} rate={1.55} /></Window>
      </Scene>
      <Scene from={31.4} to={35.1}>
        <Tag>A WORKSPACE, PLANNED BY THE MODEL</Tag>
        <Window url={HERMES} dark zoom={z(31.4, 35.1, 1.0, 1.08)} ox={50} oy={30}><Clip start={40.6} rate={1.5} /></Window>
      </Scene>

      <Scene from={35.1} to={43}>
        <Tag>PROPOSED BY THE AGENT · NOT APPLIED</Tag>
        <Window url={HERMES} dark zoom={z(35.1, 43, 1.0, 1.16)} ox={20} oy={62}><Clip start={51.6} rate={0.72} /></Window>
      </Scene>
      <Scene from={43} to={49.5}>
        <Tag tone={INK}>GITHUB, MEANWHILE · UNCHANGED</Tag>
        <Window url={GH} zoom={z(43, 49.5, 1.16, 1.5)} ox={22} oy={36}>
          <Still name="github-before-open.png" rings={[{ at: 26, x: 18.4, y: 32.4, w: 12.4, h: 4.6, color: INK }]} /></Window>
      </Scene>

      <Scene from={49.5} to={60.6}>
        <Tag tone={GREEN}>A PERSON DECIDES · WRITTEN TO GITHUB · READ BACK · VERIFIED</Tag>
        <Window url={HERMES} dark zoom={z(49.5, 60.6, 1.04, 1.1)} ox={20} oy={50}><Clip start={57.0} rate={3.0} /></Window>
      </Scene>

      <Scene from={60.6} to={63.6}>
        <Tag tone={GREEN}>GITHUB · TWO DUPLICATES CLOSED</Tag>
        <Window url={GH + "?q=state:closed"} zoom={z(60.6, 63.6, 1.1, 1.3)} ox={26} oy={40}>
          <Still name="github-after-closed.png" rings={[{ at: 16, x: 18.4, y: 32.4, w: 12.4, h: 4.6, color: GREEN }]} /></Window>
      </Scene>
      <Scene from={63.6} to={67.5}>
        <Tag tone={GREEN}>GITHUB · TITLE FIXED · BUG ASSIGNED</Tag>
        <Window url={GH} zoom={z(63.6, 67.5, 1.0, 1.1)} ox={50} oy={90}>
          <Still name="github-after-open.png" rings={[
            { at: 14, x: 18.6, y: 59.2, w: 28.6, h: 6.6, color: GREEN },
            { at: 52, x: 94.4, y: 89.2, w: 3.4, h: 5.6, color: GREEN }]} /></Window>
      </Scene>

      <Scene from={67.5} to={71.4}>
        <Tag>EVERY STEP IN A HASH-CHAINED LOG</Tag>
        <Window url={HERMES} dark zoom={z(67.5, 71.4, 1.0, 1.1)} ox={20} oy={30}><Clip start={96.2} rate={1.1} /></Window>
      </Scene>
      <Scene from={71.4} to={75.5}>
        <Tag>A NEW GOAL · A DIFFERENT INTERFACE</Tag>
        <Window url={HERMES} dark zoom={z(71.4, 75.5, 1.0, 1.08)} ox={50} oy={30}><Clip start={115.2} rate={1.25} /></Window>
      </Scene>
      <Scene from={75.5} to={79.1}>
        <Tag>ANY OPENAPI SERVICE · READ FROM ITS DOCUMENT ALONE</Tag>
        <Terminal title="MORPH OpenAPI adapter  ·  real output, abridged" lines={[
          { text: "$ python scripts/check_openapi_connector.py   # public Swagger Petstore", bold: true },
          { text: "1 entity derived from the document alone:", instant: true },
          { text: "  pet   list /pet/findByStatus   record /pet/{petId}", instant: true, color: BLUE },
          { text: "  operations: none — nothing here could be verified", instant: true, color: MUTED },
        ]} />
      </Scene>

      <Scene from={79.1} to={85.3}><Outro /></Scene>
      <Captions />

      <Audio src={staticFile("music.wav")} volume={(f) => interpolate(f, [0, S(7.5), S(8.5), S(78.8), S(79.6), S(85)], [0.5, 0.5, 0.26, 0.26, 0.5, 0.42], clamp)} />
      {narration.map((n) => (
        <Sequence key={n.id} from={S(n.at)} layout="none"><Audio src={staticFile(n.id + ".wav")} volume={1} /></Sequence>
      ))}
      {[4.3, 7.6, 11.8, 17.6, 21.6, 28.6, 35.1, 43, 49.5, 60.6, 67.5, 75.5, 79.1].map((t) => (
        <Sequence key={"w" + t} from={Math.max(0, S(t) - 7)} layout="none"><Audio src={staticFile("whoosh.wav")} volume={0.16} /></Sequence>
      ))}
      {[51.2, 55.3, 57.0, 59.3].map((t) => (
        <Sequence key={"c" + t} from={S(t)} layout="none"><Audio src={staticFile("chime.wav")} volume={0.2} /></Sequence>
      ))}
    </AbsoluteFill>
  );
};

registerRoot(() => (
  <Composition id="Morph" component={Film} durationInFrames={S(85)} fps={FPS} width={1920} height={1080} />
));
