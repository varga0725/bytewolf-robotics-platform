const runtimeStages = [
  ["01", "Perception", "Turns sensor evidence into a usable scene."],
  ["02", "Memory", "Carries mission context across every decision."],
  ["03", "Reasoning", "Interprets intent against the live environment."],
  ["04", "Mission proposal", "Produces an inspectable, bounded plan."],
  ["05", "Safety validation", "Checks the proposal before any body receives it."],
  ["06", "Body execution", "Routes approved intent through the embodiment adapter."],
  ["07", "Feedback", "Returns observations to the cognitive loop."],
] as const;

const roadmaps = [
  { name: "X500 V2", status: "Active", copy: "PX4 + Gazebo digital twin with safety-gated mission execution.", state: "active" },
  { name: "Rover", status: "Planned", copy: "Ground embodiment adapter and terrain-aware mission semantics.", state: "planned" },
  { name: "Humanoid", status: "Vision", copy: "Long-horizon assistance for work that needs a human-scale body.", state: "vision" },
] as const;

function Arrow() {
  return <span className="arrow" aria-hidden="true">↗</span>;
}

export default function App() {
  return (
    <main>
      <nav className="nav shell" aria-label="Primary navigation">
        <a className="wordmark" href="#top" aria-label="ByteWolf Robotics home"><i />BYTEWOLF</a>
        <div className="nav-links">
          <a href="#runtime">Runtime</a>
          <a href="#evidence">Evidence</a>
          <a href="#roadmap">Embodiments</a>
        </div>
        <a className="nav-cta" href="mailto:hello@bytewolf.ai?subject=Developer%20Preview">Developer preview <Arrow /></a>
      </nav>

      <section className="hero shell" id="top">
        <div className="hero-copy">
          <p className="eyebrow"><span /> Cognitive robotics platform</p>
          <h1>One Brain.<br /><em>Many Bodies.</em></h1>
          <p className="hero-text">A safety-first cognitive runtime that lets embodied systems perceive, reason and act with a shared operational memory.</p>
          <div className="hero-actions">
            <a className="button primary" href="#runtime">Explore the platform <Arrow /></a>
            <a className="button secondary" href="mailto:hello@bytewolf.ai?subject=Developer%20Preview">Request developer preview</a>
          </div>
          <div className="hero-note"><span className="pulse" /> Built for operators who need intelligence to remain accountable.</div>
        </div>

        <div className="hero-visual" aria-label="Active X500 V2 drone embodiment">
          <div className="orbital orbital-one" /><div className="orbital orbital-two" />
          <div className="signal signal-a" /><div className="signal signal-b" /><div className="signal signal-c" />
          <div className="drone" aria-hidden="true"><b /><b /><b /><b /><strong>◆</strong></div>
          <div className="visual-label top-label"><span>EMBODIMENT_01</span><b>X500 V2</b></div>
          <div className="visual-label bottom-label"><span>STATUS</span><b><i /> ACTIVE</b></div>
          <div className="coordinate"><span>47.4979° N</span><span>19.0402° E</span></div>
        </div>
      </section>

      <section className="proof shell" aria-label="Current platform status">
        <div><span className="metric">01</span><p>Active<br />embodiment</p></div>
        <div><span className="metric">7</span><p>Runtime<br />stages</p></div>
        <div><span className="metric">P0</span><p>Safety regression<br />closed</p></div>
        <div className="proof-last"><span className="small-dot" /> No direct browser-to-flight-control path</div>
      </section>

      <section className="runtime section shell" id="runtime">
        <div className="section-heading"><p className="eyebrow">The runtime</p><h2>A complete thought<br />before a movement.</h2></div>
        <p className="section-intro">ByteWolf separates cognition from actuation. Every proposed mission passes through a legible chain of evidence, constraints and feedback before it reaches a body.</p>
        <div className="runtime-flow">
          {runtimeStages.map(([number, name, copy], index) => <article className={name === "Safety validation" ? "stage safety" : "stage"} key={name}>
            <div className="stage-number">{number}<span>{index < runtimeStages.length - 1 ? "→" : "↺"}</span></div>
            <h3>{name}</h3><p>{copy}</p>
          </article>)}
        </div>
      </section>

      <section className="evidence section" id="evidence">
        <div className="shell evidence-layout">
          <div className="evidence-copy"><p className="eyebrow">Technical credibility</p><h2>Calm at the interface.<br /><em>Strict underneath.</em></h2><p>Our first embodiment is developed against a real PX4 and Gazebo digital twin. Safety checks happen before an adapter call, telemetry fails closed, and mission artifacts leave a reviewable trail.</p><a className="text-link" href="mailto:hello@bytewolf.ai?subject=Technical%20brief">Get the technical brief <Arrow /></a></div>
          <div className="build-panel">
            <div className="panel-head"><span>BUILD STATUS / X500 V2</span><b><i /> OPERATIONAL TWIN</b></div>
            <div className="build-grid"><div><small>CONTROL PLANE</small><strong>PX4 SITL</strong></div><div><small>SIMULATION</small><strong>Gazebo Harmonic</strong></div><div><small>MISSION GATE</small><strong>Fail-closed</strong></div><div><small>TELEMETRY</small><strong>Read-only</strong></div></div>
            <div className="test-line"><span>Safety regression matrix</span><b>10 / 10</b><div><i /></div></div>
            <p className="panel-foot">Simulation evidence informs engineering confidence. It is not a claim of physical flight readiness.</p>
          </div>
        </div>
      </section>

      <section className="roadmap section shell" id="roadmap">
        <div className="section-heading"><p className="eyebrow">Embodiment roadmap</p><h2>Designed to travel<br />between bodies.</h2></div>
        <div className="roadmap-grid">{roadmaps.map((item) => <article className={`roadmap-card ${item.state}`} key={item.name}><div className="roadmap-top"><span>{item.status}</span><b>{item.state === "active" ? "◈" : item.state === "planned" ? "◇" : "◎"}</b></div><h3>{item.name}</h3><p>{item.copy}</p><div className="roadmap-line"><i /></div><small>{item.state === "active" ? "Available now" : item.state === "planned" ? "Next embodiment" : "Research horizon"}</small></article>)}</div>
      </section>

      <section className="closing shell"><div><p className="eyebrow">Build with ByteWolf</p><h2>Put a considered brain<br />inside your next body.</h2></div><a className="button primary" href="mailto:hello@bytewolf.ai?subject=Developer%20Preview">Request developer preview <Arrow /></a></section>
      <footer className="shell"><a className="wordmark" href="#top"><i />BYTEWOLF</a><p>Robotics intelligence, bounded by evidence.</p><a href="mailto:hello@bytewolf.ai?subject=Contact">hello@bytewolf.ai</a></footer>
    </main>
  );
}
