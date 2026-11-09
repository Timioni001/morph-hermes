import React from "react";
import {
  AbsoluteFill, Audio, Composition, Easing, Img, OffthreadVideo, Sequence,
  interpolate, registerRoot, spring, staticFile, useCurrentFrame, useVideoConfig,
} from "remotion";
import narration from "../narration.json";

const FPS = 30;
const S = (seconds) => Math.round(seconds * FPS);
const INK = "#0b1220";
const MUTED = "#64748b";
const BLUE = "#2563eb";
const SANS = 'Inter, "Inter Display", "DejaVu Sans", sans-serif';
const MONO = '"DejaVu Sans Mono", monospace';
const ease = Easing.bezier(0.22, 1, 0.36, 1);
const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" };
const ramp = (frame, from, to, a = 0, b = 1) =>
  interpolate(frame, [from, to], [a, b], { ...clamp, easing: ease });

/* ── the white stage, with slow fluid lines ───────────────────────────────── */
const Stage = () => {
  const frame = useCurrentFrame();
  const lines = [0, 1, 2, 3, 4].map((i) => {
    const t = frame / 90 + i * 1.3;
    const y = 540 + Math.sin(t) * 70 + (i - 2) * 150;
    const c1 = 300 + Math.cos(t * 0.7) * 160;
    const c2 = 780 + Math.sin(t * 0.9 + i) * 180;
    return <path key={i} d={`M -100 ${y} C 500 ${c1}, 1300 ${c2}, 2020 ${y + Math.cos(t) * 90}`}
      fill="none" stroke={BLUE} strokeOpacity={0.05 + i * 0.012} strokeWidth={2} />;
  });
  return (
    <AbsoluteFill style={{ background: "radial-gradient(1200px 700px at 50% 38%, #ffffff 0%, #f5f7fb 100%)" }}>
      <svg width={1920} height={1080}>{lines}</svg>
    </AbsoluteFill>
  );
};

/* ── a scene that fades and lifts in, and out ─────────────────────────────── */
const Scene = ({ from, to, children }) => {
  const frame = useCurrentFrame();
  const a = S(from), b = S(to);
  if (frame < a - 2 || frame > b + 2) { return null; }
  const enter = ramp(frame, a, a + 16);
  const exit = ramp(frame, b - 12, b, 1, 0);
  const o = Math.min(enter, exit);
  return (
    <AbsoluteFill style={{ opacity: o, transform: `translateY(${(1 - enter) * 28}px) scale(${0.985 + 0.015 * enter})` }}>
      <Sequence from={a} layout="none">{children}</Sequence>
    </AbsoluteFill>
  );
};

/* ── a browser window around real footage ─────────────────────────────────── */
const Window = ({ url, children, width = 1440, zoom = 1, ox = 50, oy = 50, dark = false }) => {
  const height = Math.round((width * 9) / 16);
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-start", paddingTop: 108 }}>
      <div style={{ width, borderRadius: 18, overflow: "hidden", background: dark ? "#0f1115" : "#fff",
        boxShadow: "0 40px 90px rgba(15,23,42,.18), 0 2px 0 rgba(15,23,42,.06)",
        border: "1px solid rgba(15,23,42,.10)" }}>
        <div style={{ height: 44, display: "flex", alignItems: "center", gap: 8, padding: "0 16px",
          background: "#f1f4f9", borderBottom: "1px solid rgba(15,23,42,.08)" }}>
          {["#e5e7eb", "#e5e7eb", "#e5e7eb"].map((c, i) =>
            <div key={i} style={{ width: 11, height: 11, borderRadius: 6, background: c }} />)}
          <div style={{ marginLeft: 14, flex: 1, height: 26, borderRadius: 13, background: "#fff",
            border: "1px solid rgba(15,23,42,.08)", display: "flex", alignItems: "center",
            padding: "0 14px", fontFamily: SANS, fontSize: 14, color: MUTED }}>{url}</div>
        </div>
        <div style={{ width, height: height - 44, overflow: "hidden", position: "relative" }}>
          <div style={{ width: "100%", height: "100%", transform: `scale(${zoom})`,
            transformOrigin: `${ox}% ${oy}%` }}>{children}</div>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// `full` shows the whole Hermes dashboard; otherwise the frame is the MORPH tab itself
// (the host's navigation column is cropped away so the interface is readable).
const Clip = ({ start, rate = 1, full = false }) => (
  <OffthreadVideo src={staticFile("dashboard.mp4")} startFrom={S(start)} playbackRate={rate} muted
    style={full ? { width: "100%", display: "block" }
      : { position: "absolute", right: 0, top: -42, width: "121%", display: "block" }} />
);
const Still = ({ name }) => (
  <Img src={staticFile(name)} style={{ width: "100%", display: "block" }} />
);

/* ── small label above a window ───────────────────────────────────────────── */
const Tag = ({ children, tone = BLUE, top = 34 }) => (
  <div style={{ position: "absolute", top, left: 0, right: 0, display: "flex", justifyContent: "center" }}>
    <div style={{ fontFamily: SANS, fontSize: 19, fontWeight: 600, letterSpacing: 2.2, color: tone,
      background: "#fff", border: `1.5px solid ${tone}22`, borderRadius: 999, padding: "9px 22px",
      boxShadow: "0 6px 18px rgba(15,23,42,.06)" }}>{children}</div>
  </div>
);

/* ── a terminal card that types real output ───────────────────────────────── */
const Terminal = ({ lines, title }) => {
  const frame = useCurrentFrame();
  let budget = frame * 2.6;
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", paddingBottom: 70 }}>
      <div style={{ width: 1320, borderRadius: 18, background: "#fff", border: "1px solid rgba(15,23,42,.10)",
        boxShadow: "0 40px 90px rgba(15,23,42,.16)", overflow: "hidden" }}>
        <div style={{ height: 44, background: "#f1f4f9", borderBottom: "1px solid rgba(15,23,42,.08)",
          display: "flex", alignItems: "center", padding: "0 18px", fontFamily: SANS, fontSize: 15, color: MUTED }}>
          {title}</div>
        <div style={{ padding: "30px 38px 36px", fontFamily: MONO, fontSize: 25, lineHeight: 1.62, color: INK, minHeight: 300 }}>
          {lines.map((line, i) => {
            const text = line.text;
            const shown = line.instant ? (budget > 0 ? text : "") : text.slice(0, Math.max(0, Math.floor(budget)));
            budget -= line.instant ? 14 : text.length + 8;
            if (!shown) { return null; }
            return <div key={i} style={{ color: line.color || INK, fontWeight: line.bold ? 700 : 400,
              whiteSpace: "pre-wrap" }}>{shown}</div>;
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
};

/* ── intro: the question, then the mark ───────────────────────────────────── */
const Word = ({ children, at, color = INK }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: frame - at, fps, config: { damping: 18, stiffness: 120 } });
  return <span style={{ display: "inline-block", marginRight: 26, color, opacity: p,
    transform: `translateY(${(1 - p) * 46}px)`, filter: `blur(${(1 - p) * 8}px)` }}>{children}</span>;
};

const Mark = ({ at = 0, size = 150 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: frame - at, fps, config: { damping: 16, stiffness: 90 } });
  const morph = interpolate(Math.sin((frame - at) / 22), [-1, 1], [50, 26]);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 34, opacity: p, transform: `scale(${0.9 + 0.1 * p})` }}>
      <div style={{ width: size * 0.62, height: size * 0.62, borderRadius: `${morph}%`, background: INK,
        transform: `rotate(${(1 - p) * -90}deg)`, position: "relative" }}>
        <div style={{ position: "absolute", inset: "30%", borderRadius: `${76 - morph}%`, background: BLUE }} />
      </div>
      <div style={{ fontFamily: SANS, fontWeight: 800, fontSize: size, letterSpacing: interpolate(p, [0, 1], [30, 10]),
        color: INK }}>MORPH</div>
    </div>
  );
};

const Intro = () => {
  const frame = useCurrentFrame();
  const out = ramp(frame, S(4.1), S(4.6), 1, 0);
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
      <div style={{ position: "absolute", opacity: out, transform: `scale(${1 + (1 - out) * 0.06})`,
        fontFamily: SANS, fontWeight: 700, fontSize: 118, letterSpacing: -3, textAlign: "center", lineHeight: 1.12 }}>
        <div><Word at={S(0.7)}>AI</Word><Word at={S(0.95)}>can</Word><Word at={S(1.2)}>act.</Word></div>
        <div><Word at={S(2.0)} color={BLUE}>But</Word><Word at={S(2.2)} color={BLUE}>who</Word>
          <Word at={S(2.4)} color={BLUE}>decides?</Word></div>
      </div>
      {frame > S(4.5) ? <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 30 }}>
        <Mark at={S(4.6)} />
        <div style={{ height: 3, width: ramp(frame, S(5.2), S(6.2), 0, 560), background: BLUE, borderRadius: 2 }} />
        <div style={{ fontFamily: SANS, fontSize: 38, color: MUTED, opacity: ramp(frame, S(5.6), S(6.4)) }}>
          for Hermes Agent</div>
      </div> : null}
    </AbsoluteFill>
  );
};

const Outro = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 34 }}>
      <Mark at={4} size={170} />
      <div style={{ height: 3, width: ramp(frame, 20, 55, 0, 640), background: BLUE, borderRadius: 2 }} />
      <div style={{ fontFamily: SANS, fontWeight: 600, fontSize: 54, color: INK, opacity: ramp(frame, 28, 52),
        transform: `translateY(${ramp(frame, 28, 52, 20, 0)}px)` }}>Software that adapts to you.</div>
      <div style={{ fontFamily: SANS, fontSize: 30, color: MUTED, letterSpacing: 1, opacity: ramp(frame, 55, 80) }}>
        Powered by Hermes Agent</div>
    </AbsoluteFill>
  );
};

/* ── one-line statement card ──────────────────────────────────────────────── */
const Statement = ({ children }) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", paddingBottom: 60 }}>
      <div style={{ width: 1400, textAlign: "center", fontFamily: SANS, fontWeight: 700, fontSize: 68,
        lineHeight: 1.18, letterSpacing: -1.5, color: INK, opacity: ramp(frame, 0, 18) }}>{children}</div>
    </AbsoluteFill>
  );
};

/* ── captions, synchronised with the narration ────────────────────────────── */
const Captions = () => {
  const frame = useCurrentFrame();
  const line = narration.find((n) => frame >= S(n.at) - 2 && frame <= S(n.at + n.dur) + 8);
  if (!line || line.id === "n1" || line.id === "n10") { return null; }
  const o = Math.min(ramp(frame, S(line.at) - 2, S(line.at) + 6), ramp(frame, S(line.at + line.dur), S(line.at + line.dur) + 8, 1, 0));
  const text = line.text.replace(/A\.I\./g, "AI").replace(/Open A\.P\.I\./g, "OpenAPI").replace(/A\.P\.I\./g, "API").replace(/Morph/g, "MORPH");
  return (
    <div style={{ position: "absolute", bottom: 38, left: 0, right: 0, display: "flex", justifyContent: "center", opacity: o }}>
      <div style={{ maxWidth: 1500, fontFamily: SANS, fontSize: 31, fontWeight: 500, color: INK, textAlign: "center",
        background: "rgba(255,255,255,.94)", border: "1px solid rgba(15,23,42,.08)", borderRadius: 16,
        padding: "13px 28px", boxShadow: "0 10px 30px rgba(15,23,42,.08)" }}>{text}</div>
    </div>
  );
};

const HERMES = "localhost:9119/morph  ·  Hermes dashboard";
const GH = "github.com/Timioni001/morph-demo/issues";
const GREEN = "#059669";

const Film = () => {
  const frame = useCurrentFrame();
  const z = (from, to, a, b) => ramp(frame, S(from), S(to), a, b);
  return (
    <AbsoluteFill style={{ background: "#fff" }}>
      <Stage />
      <Scene from={0} to={8}><Intro /></Scene>

      <Scene from={8} to={12.6}>
        <Statement>One adaptive workspace across your applications.<br />
          <span style={{ color: BLUE }}>You stay in control of every change.</span></Statement>
      </Scene>
      <Scene from={12.6} to={18}>
        <Tag>CONNECTED · CAPABILITIES DISCOVERED FROM THE API</Tag>
        <Window url={HERMES} dark zoom={z(12.6, 18, 1.0, 1.1)} ox={0} oy={20}><Clip start={4.4} full /></Window>
      </Scene>

      <Scene from={18} to={22.1}>
        <Tag tone={INK}>A REAL GITHUB REPOSITORY</Tag>
        <Window url={GH} zoom={z(18, 22.1, 1.0, 1.16)} ox={30} oy={30}><Still name="github-before-open.png" /></Window>
      </Scene>
      <Scene from={22.1} to={29.5}>
        <Tag>THE HERMES AGENT, WORKING THROUGH MORPH</Tag>
        <Terminal title="hermes chat  ·  real session output" lines={[
          { text: '$ hermes chat -q "Triage the GitHub issues… propose each change; do not approve."', bold: true },
          { text: "⚡ morph_discover", color: MUTED, instant: true },
          { text: "⚡ morph_inspect_record  ×4", color: MUTED, instant: true },
          { text: "⚡ morph_propose_action  ×4", color: BLUE, instant: true },
          { text: "☤ All four proposals are queued as pending:" },
          { text: "  #3, #6 — close as not planned, duplicate of #1" },
          { text: "  #7 — retitle   ·   #1 — assign to Timioni001" },
          { text: "  Nothing has been applied. Waiting for your approval.", color: GREEN },
        ]} />
      </Scene>
      <Scene from={29.5} to={35.2}>
        <Tag>WORKSPACE COMPOSED FOR THE GOAL · PLANNED BY THE MODEL</Tag>
        <Window url={HERMES} dark zoom={z(29.5, 35.2, 1.04, 1.12)} ox={0} oy={30}><Clip start={14.6} rate={1.6} /></Window>
      </Scene>

      <Scene from={35.2} to={43.2}>
        <Tag>PROPOSED BY THE AGENT · NOT APPLIED</Tag>
        <Window url={HERMES} dark zoom={z(35.2, 43.2, 1.0, 1.2)} ox={0} oy={30}><Clip start={25.2} rate={0.62} /></Window>
      </Scene>
      <Scene from={43.2} to={50}>
        <Tag tone={INK}>GITHUB, MEANWHILE · 10 OPEN, 0 CLOSED · UNCHANGED</Tag>
        <Window url={GH} zoom={z(43.2, 50, 1.18, 1.42)} ox={24} oy={30}><Still name="github-before-open.png" /></Window>
      </Scene>

      <Scene from={50} to={60.5}>
        <Tag tone={GREEN}>A PERSON APPROVES · WRITTEN TO GITHUB · READ BACK · VERIFIED</Tag>
        <Window url={HERMES} dark zoom={z(50, 60.5, 1.08, 1.16)} ox={0} oy={35}><Clip start={29.6} rate={2.9} /></Window>
      </Scene>
      <Scene from={60.5} to={63.2}>
        <Tag tone={GREEN}>GITHUB · TWO DUPLICATES CLOSED</Tag>
        <Window url={GH + "?q=state:closed"} zoom={z(60.5, 63.2, 1.14, 1.3)} ox={28} oy={34}><Still name="github-after-closed.png" /></Window>
      </Scene>
      <Scene from={63.2} to={65.6}>
        <Tag tone={GREEN}>GITHUB · TITLE FIXED</Tag>
        <Window url="github.com/Timioni001/morph-demo/issues/7" zoom={z(63.2, 65.6, 1.12, 1.26)} ox={30} oy={22}><Still name="github-after-issue7.png" /></Window>
      </Scene>
      <Scene from={65.6} to={68}>
        <Tag tone={GREEN}>GITHUB · BUG ASSIGNED</Tag>
        <Window url="github.com/Timioni001/morph-demo/issues/1" zoom={z(65.6, 68, 1.1, 1.24)} ox={78} oy={26}><Still name="github-after-issue1.png" /></Window>
      </Scene>

      <Scene from={68} to={72}>
        <Tag>AUDIT TRAIL · HASH-CHAINED</Tag>
        <Window url={HERMES} dark zoom={z(68, 72, 1.04, 1.14)} ox={0} oy={30}><Clip start={64.8} rate={1.1} /></Window>
      </Scene>
      <Scene from={72} to={75.6}>
        <Tag>A NEW GOAL · A DIFFERENT INTERFACE</Tag>
        <Window url={HERMES} dark zoom={z(72, 75.6, 1.04, 1.12)} ox={0} oy={32}><Clip start={93.4} rate={1.5} /></Window>
      </Scene>
      <Scene from={75.6} to={79.2}>
        <Tag>ANY OPENAPI SERVICE · READ FROM ITS DOCUMENT ALONE</Tag>
        <Terminal title="real output  ·  public Swagger Petstore" lines={[
          { text: "$ hermes morph connect openapi https://petstore3.swagger.io/api/v3", bold: true },
          { text: "1 entity derived from the document alone:", instant: true },
          { text: "  pet   list /pet/findByStatus   record /pet/{petId}", instant: true, color: BLUE },
          { text: "  operations: none — nothing here could be verified", instant: true, color: MUTED },
        ]} />
      </Scene>

      <Scene from={79.2} to={85.2}><Outro /></Scene>
      <Captions />

      <Audio src={staticFile("pad.wav")} volume={0.16} />
      {narration.map((n) => (
        <Sequence key={n.id} from={S(n.at)} layout="none"><Audio src={staticFile(n.id + ".wav")} volume={1} /></Sequence>
      ))}
      {[4.5, 8, 12.6, 18, 22.1, 29.5, 35.2, 43.2, 50, 60.5, 68, 72, 75.6, 79.2].map((t) => (
        <Sequence key={"w" + t} from={S(t) - 6} layout="none"><Audio src={staticFile("whoosh.wav")} volume={0.22} /></Sequence>
      ))}
      {[52.1, 55.2, 57.6, 60.0].map((t) => (
        <Sequence key={"c" + t} from={S(t)} layout="none"><Audio src={staticFile("chime.wav")} volume={0.3} /></Sequence>
      ))}
    </AbsoluteFill>
  );
};

registerRoot(() => (
  <Composition id="Morph" component={Film} durationInFrames={S(85)} fps={FPS} width={1920} height={1080} />
));
