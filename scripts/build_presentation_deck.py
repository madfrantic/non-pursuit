#!/usr/bin/env python3
"""
Non-Pursuit Presentation Deck Generator
Generates unified HTML slides for GitHub Pages (docs/ and root)
Matching the architectural dossier theme of BIO_ME.pdf
"""
import os
import shutil

# 1. Ensure assets/slides has all 18 slides from BIO_ME.pdf
slide_files = [f"assets/slides/slide-{i:02d}.png" for i in range(1, 19)]
for s in slide_files:
    if not os.path.exists(s):
        print(f"Warning: {s} does not exist!")

# 2. Sync to docs/assets
os.makedirs("docs/assets/slides", exist_ok=True)
for s in slide_files:
    dst = os.path.join("docs", s)
    shutil.copy2(s, dst)

# Copy logos if present
for logo in ["assets/logo_shield.png", "assets/logo_wordmark.png"]:
    if os.path.exists(logo):
        shutil.copy2(logo, os.path.join("docs", logo))

# Build Slide Sections
# Slide 1: Intro Page (About Me / Builder Profile per class instructions)
# Slides 2-19: BIO_ME.pdf Pages 1-18
# Slide 20: YouTube Video Demo (https://youtu.be/V5qqb-L7ack)
# Slide 21: Live Streamlit App Launch (https://non-pursuit.streamlit.app/)

html_template = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NON-PURSUIT — Digital Sovereignty & Autonomous Compliance</title>
<link rel="icon" href="assets/logo_shield.png">
<style>
:root {
  --bg-dark:    #070A11;
  --bg-panel:   #0F1523;
  --bg-dossier: #F8F9FA;
  --border-line:#1A233A;
  --grid-line:  rgba(255,255,255,0.06);
  --grid-light: rgba(0,0,0,0.06);
  --brass:      #D4AF37;
  --brass-light:#FFE58F;
  --crimson:    #E74C3C;
  --ink:        #0E1118;
  --text-dim:   #8E9AA8;
  --text-muted: #5C6777;
  --text-main:  #E8ECEF;
  --mono:       ui-monospace, "SF Mono", "Fira Code", "Courier New", monospace;
  --sans:       "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --serif:      "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
  --ease:       cubic-bezier(.16, 1, .3, 1);
}

* { box-sizing: border-box; margin: 0; padding: 0; }

html, body {
  height: 100%; width: 100%;
  background: var(--bg-dark);
  color: var(--text-main);
  font-family: var(--sans);
  overflow: hidden;
  user-select: none;
}

/* Background grid styling */
#deck {
  position: fixed; inset: 0;
  background-color: var(--bg-dark);
  background-image: 
    linear-gradient(var(--grid-line) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid-line) 1px, transparent 1px);
  background-size: 32px 32px;
  display: flex; align-items: center; justify-content: center;
}

.slide {
  position: absolute; inset: 0;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 55px 35px 65px;
  opacity: 0; visibility: hidden;
  transform: translate3d(0, 12px, 0) scale(0.99);
  transition: opacity .4s var(--ease), transform .45s var(--ease), visibility 0s linear .45s;
  z-index: 1;
}

.slide.active {
  opacity: 1; visibility: visible;
  transform: translate3d(0, 0, 0) scale(1);
  transition: opacity .4s var(--ease), transform .45s var(--ease), visibility 0s linear 0s;
  z-index: 2;
}

/* Slide image container for BIO_ME.pdf slides */
.slide-img-box {
  width: 100%; height: 100%;
  max-width: 1380px; max-height: 84vh;
  display: flex; align-items: center; justify-content: center;
  position: relative;
}

.slide-img-box img {
  max-width: 100%; max-height: 100%;
  width: auto; height: auto;
  object-fit: contain;
  border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.12);
  box-shadow: 0 16px 50px rgba(0,0,0,0.85), 0 0 30px rgba(212,175,55,0.08);
}

/* Dossier HTML Slide Container (Matching BIO_ME Theme) */
.dossier-card {
  width: 100%;
  max-width: 1280px;
  max-height: 84vh;
  aspect-ratio: 16/9;
  background: var(--bg-dossier);
  color: var(--ink);
  border-radius: 4px;
  border: 2px solid #000;
  box-shadow: 0 16px 50px rgba(0,0,0,0.85), 0 0 35px rgba(212,175,55,0.1);
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  padding: clamp(24px, 3.5vw, 48px);
  background-image: 
    linear-gradient(var(--grid-light) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid-light) 1px, transparent 1px);
  background-size: 28px 28px;
}

/* Top Dossier Case Header */
.dossier-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  border-bottom: 2px solid #000;
  padding-bottom: 16px;
  margin-bottom: 24px;
}

.dossier-case-tag {
  font-family: var(--mono);
  font-size: clamp(11px, 1.1vw, 14px);
  font-weight: 700;
  letter-spacing: 0.12em;
  line-height: 1.5;
  color: #111;
}

.dossier-case-tag span {
  display: block;
  font-size: clamp(10px, 0.95vw, 12px);
  color: #555;
  font-weight: 500;
}

/* Classified Stamp */
.stamp {
  font-family: var(--mono);
  font-weight: 900;
  text-transform: uppercase;
  letter-spacing: 0.18em;
  padding: 6px 14px;
  border: 3px solid var(--crimson);
  color: var(--crimson);
  border-radius: 4px;
  transform: rotate(-3deg);
  display: inline-block;
  font-size: clamp(12px, 1.2vw, 16px);
  box-shadow: inset 0 0 0 1px var(--crimson);
}

.stamp.blue {
  border-color: #1E40AF;
  color: #1E40AF;
  box-shadow: inset 0 0 0 1px #1E40AF;
  transform: rotate(2deg);
}

.stamp.green {
  border-color: #047857;
  color: #047857;
  box-shadow: inset 0 0 0 1px #047857;
  transform: rotate(-1.5deg);
}

/* Intro Body Layout */
.intro-grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: clamp(20px, 3vw, 40px);
  flex: 1;
  align-items: center;
}

.intro-left h1 {
  font-size: clamp(28px, 3.4vw, 44px);
  font-weight: 800;
  line-height: 1.15;
  letter-spacing: -0.02em;
  margin-bottom: 8px;
  color: #000;
}

.intro-left .subtitle {
  font-family: var(--mono);
  font-size: clamp(13px, 1.2vw, 16px);
  color: #374151;
  margin-bottom: 24px;
  font-weight: 600;
  letter-spacing: 0.05em;
}

.intro-details-table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--mono);
  font-size: clamp(12px, 1.1vw, 14px);
  background: rgba(255,255,255,0.7);
  border: 1px solid #000;
}

.intro-details-table td {
  padding: 10px 14px;
  border-bottom: 1px solid #000;
  vertical-align: top;
}

.intro-details-table tr:last-child td {
  border-bottom: none;
}

.intro-details-table td.label {
  font-weight: 700;
  background: #000;
  color: #FFF;
  width: 32%;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.intro-details-table td.value {
  color: #111;
  line-height: 1.45;
}

.intro-right {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.mission-box {
  background: #FFF;
  border: 1.5px solid #000;
  padding: 20px;
  box-shadow: 4px 4px 0px #000;
}

.mission-box h3 {
  font-family: var(--mono);
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: #111;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.mission-box p {
  font-size: clamp(12px, 1.15vw, 14px);
  line-height: 1.55;
  color: #222;
}

.metric-strip {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.metric-card {
  background: #000;
  color: #FFF;
  padding: 14px;
  border-radius: 2px;
  font-family: var(--mono);
}

.metric-card .num {
  font-size: clamp(20px, 2.2vw, 28px);
  font-weight: 800;
  color: var(--brass-light);
  line-height: 1;
  margin-bottom: 4px;
}

.metric-card .lbl {
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: #9CA3AF;
}

.dossier-footer {
  margin-top: auto;
  border-top: 1px solid #000;
  padding-top: 10px;
  display: flex;
  justify-content: space-between;
  font-family: var(--mono);
  font-size: 11px;
  color: #444;
  letter-spacing: 0.08em;
}

/* Video Frame Slide (YouTube Embed) */
.video-card {
  width: 100%;
  max-width: 1280px;
  max-height: 84vh;
  aspect-ratio: 16/9;
  background: #0B0F19;
  border: 2px solid #222B40;
  border-radius: 6px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.9), 0 0 35px rgba(212,175,55,0.12);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
}

.video-bar {
  background: #111726;
  border-bottom: 1px solid #222B40;
  padding: 10px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-family: var(--mono);
  font-size: 12px;
  letter-spacing: 0.1em;
}

.video-bar .title {
  color: var(--brass-light);
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 10px;
}

.video-bar .ext-btn {
  color: #FFF;
  text-decoration: none;
  background: #1E293B;
  border: 1px solid #334155;
  padding: 4px 12px;
  border-radius: 3px;
  font-size: 11px;
  transition: all 0.2s ease;
}

.video-bar .ext-btn:hover {
  background: var(--crimson);
  border-color: var(--crimson);
}

.video-body {
  flex: 1;
  position: relative;
  background: #000;
}

.video-body iframe {
  width: 100%;
  height: 100%;
  border: none;
}

/* Launch App Call to Action Slide */
.cta-card {
  width: 100%;
  max-width: 1100px;
  max-height: 84vh;
  background: var(--bg-dossier);
  color: var(--ink);
  border: 2px solid #000;
  border-radius: 4px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.9), 0 0 40px rgba(212,175,55,0.15);
  padding: clamp(32px, 4.5vh, 52px) clamp(24px, 4vw, 56px);
  text-align: center;
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background-image: 
    linear-gradient(var(--grid-light) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid-light) 1px, transparent 1px);
  background-size: 28px 28px;
}

.cta-header-stamp {
  margin-bottom: 16px;
}

.cta-title {
  font-size: clamp(30px, 4.2vw, 52px);
  font-weight: 900;
  letter-spacing: -0.02em;
  line-height: 1.1;
  color: #000;
  margin-bottom: 12px;
}

.cta-subtitle {
  font-family: var(--mono);
  font-size: clamp(12px, 1.2vw, 15px);
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: #4B5563;
  margin-bottom: 32px;
  font-weight: 600;
}

.launch-btn-main {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  background: #000;
  color: #FFF;
  font-family: var(--mono);
  font-size: clamp(15px, 1.5vw, 20px);
  font-weight: 800;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  padding: clamp(16px, 2vh, 22px) clamp(32px, 4vw, 48px);
  border-radius: 3px;
  text-decoration: none;
  border: 2px solid #000;
  box-shadow: 6px 6px 0px var(--crimson);
  transition: all 0.2s ease;
  margin-bottom: 28px;
}

.launch-btn-main:hover {
  transform: translate(-2px, -2px);
  box-shadow: 8px 8px 0px var(--crimson);
  background: #111;
  color: var(--brass-light);
}

.launch-btn-main:active {
  transform: translate(2px, 2px);
  box-shadow: 2px 2px 0px var(--crimson);
}

.cta-links-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 20px;
  font-family: var(--mono);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: #374151;
  margin-top: 10px;
}

.cta-links-row a {
  color: #000;
  text-decoration: none;
  border-bottom: 2px solid #000;
  padding-bottom: 2px;
  transition: all 0.2s ease;
}

.cta-links-row a:hover {
  color: var(--crimson);
  border-color: var(--crimson);
}

/* Chrome HUD */
.hud {
  position: fixed; z-index: 40;
  font-family: var(--mono);
  font-size: 11px; letter-spacing: .22em;
  text-transform: uppercase;
  color: rgba(232,236,239,0.75);
}

#hud-tl { top: 16px; left: 24px; display: flex; align-items: center; gap: 12px; }
#hud-tl img { height: 26px; width: auto; filter: drop-shadow(0 2px 8px rgba(0,0,0,0.6)); }
#hud-tl b { color: var(--brass-light); }

#hud-tr { top: 16px; right: 24px; display: flex; align-items: center; gap: 16px; }
#hud-tr .app-link {
  color: #000;
  text-decoration: none;
  background: var(--brass);
  border: 1px solid var(--brass-light);
  padding: 6px 14px;
  border-radius: 2px;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .14em;
  transition: all .2s ease;
  box-shadow: 0 2px 8px rgba(212,175,55,0.3);
}

#hud-tr .app-link:hover {
  background: var(--brass-light);
  transform: translateY(-1px);
}

#hud-bl { bottom: 16px; left: 24px; color: var(--text-dim); }
#hud-br { bottom: 16px; right: 24px; display: flex; align-items: center; gap: 16px; }

.dots { display: flex; gap: 5px; }
.dot { width: 10px; height: 4px; background: rgba(255,255,255,0.2); border-radius: 1px; cursor: pointer; transition: all .25s ease; }
.dot.on { background: var(--brass-light); width: 22px; }

/* Navigation Buttons */
.nav-arrow {
  position: fixed; top: 50%; transform: translateY(-50%);
  z-index: 50;
  background: rgba(15,21,35,0.85);
  border: 1px solid rgba(255,255,255,0.15);
  color: var(--brass-light);
  width: 46px; height: 60px;
  display: flex; align-items: center; justify-content: center;
  font-size: 22px;
  cursor: pointer;
  border-radius: 3px;
  transition: all .2s ease;
  backdrop-filter: blur(6px);
}

.nav-arrow:hover {
  background: #1A243C;
  border-color: var(--brass-light);
  transform: translateY(-50%) scale(1.05);
}

#prev-btn { left: 14px; }
#next-btn { right: 14px; }

@media (max-width: 768px) {
  .nav-arrow { display: none; }
  .hud { font-size: 9px; }
  #hud-tl { left: 12px; top: 10px; }
  #hud-tr { right: 12px; top: 10px; }
  #hud-bl { left: 12px; bottom: 10px; }
  #hud-br { right: 12px; bottom: 10px; }
  .intro-grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>

<div id="deck">

  <!-- Slide 01: About Me / Builder Intro (Per Class Instructions) -->
  <section class="slide active" id="s1">
    <div class="dossier-card">
      <div class="dossier-header">
        <div class="dossier-case-tag">
          CASE NO. 2026-CCPA-08 // BUILDER DOSSIER
          <span>PURSUIT x CUNY LaGUARDIA COMMUNITY COLLEGE — AI FUNDAMENTALS</span>
        </div>
        <div class="stamp">CONFIDENTIAL SEALED</div>
      </div>
      
      <div class="intro-grid">
        <div class="intro-left">
          <h1>John Cuentas</h1>
          <div class="subtitle">&gt; AI-Assisted Builder &amp; Software Engineer</div>
          
          <table class="intro-details-table">
            <tr>
              <td class="label">Project</td>
              <td class="value"><strong>NON-PURSUIT</strong> — The Sovereign Engine</td>
            </tr>
            <tr>
              <td class="label">Objective</td>
              <td class="value">Building local-first, zero-knowledge privacy infrastructure and automated statutory compliance engines.</td>
            </tr>
            <tr>
              <td class="label">Target User</td>
              <td class="value">Privacy-conscious citizens, investigators, and consumers trapped in commercial data broker subscriptions.</td>
            </tr>
            <tr>
              <td class="label">Core Metric</td>
              <td class="value"><strong>100% Zero-Knowledge</strong> local execution · <strong>1,070+</strong> automated test verifications.</td>
            </tr>
          </table>
        </div>
        
        <div class="intro-right">
          <div class="mission-box">
            <h3><span>🛡️</span> AI Skills &amp; Thesis</h3>
            <p>
              Transforming AI from conversational chatbots into autonomous multi-agent terminal harnesses. Building automated statutory workflows (CCPA/GDPR) to permanently delete public data footprints without third-party subscriptions.
            </p>
          </div>
          
          <div class="metric-strip">
            <div class="metric-card">
              <div class="num">3 Min</div>
              <div class="lbl">Demo Day Presentation</div>
            </div>
            <div class="metric-card">
              <div class="num">100%</div>
              <div class="lbl">Local-First Vault</div>
            </div>
          </div>
        </div>
      </div>
      
      <div class="dossier-footer">
        <div>SUBJECT: JOHN CUENTAS // DEMO DAY 2026</div>
        <div>AUTHORIZED FOR CLASS PRESENTATION</div>
        <div>non-pursuit.streamlit.app</div>
      </div>
    </div>
  </section>

  <!-- Slides 2-19: BIO_ME.pdf Pages 1-18 -->
"""

# Append the 18 PDF slide pages
slide_items = []
for i in range(1, 19):
    slide_num = i + 1  # Since slide 1 is Intro
    slide_path = f"assets/slides/slide-{i:02d}.png"
    slide_items.append(f"""  <!-- Slide {slide_num:02d}: BIO_ME.pdf Page {i:02d} -->
  <section class="slide" id="s{slide_num}">
    <div class="slide-img-box">
      <img src="{slide_path}" alt="Non-Pursuit Presentation Slide {i}">
    </div>
  </section>""")

html_template += "\n".join(slide_items)

# Add YouTube Video Demo Slide (Slide 20) and Live App Slide (Slide 21)
html_template += """

  <!-- Slide 20: YouTube Video Demo -->
  <section class="slide" id="s20">
    <div class="video-card">
      <div class="video-bar">
        <div class="title">
          <span>📹</span>
          <span>NON-PURSUIT // OFFICIAL DEMO VIDEO (YOUtu.be/V5qqb-L7ack)</span>
        </div>
        <a href="https://youtu.be/V5qqb-L7ack" target="_blank" rel="noopener noreferrer" class="ext-btn">
          ↗ Open on YouTube
        </a>
      </div>
      <div class="video-body">
        <iframe 
          src="https://www.youtube.com/embed/V5qqb-L7ack?rel=0" 
          title="Non-Pursuit Demo Video"
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" 
          allowfullscreen>
        </iframe>
      </div>
    </div>
  </section>

  <!-- Slide 21: Live Streamlit App Launch & Call To Action -->
  <section class="slide" id="s21">
    <div class="cta-card">
      <div class="cta-header-stamp">
        <div class="stamp green">STATUS: LIVE IN PRODUCTION</div>
      </div>
      <h1 class="cta-title">non-pursuit. the sovereign engine</h1>
      <div class="cta-subtitle">Local-First Autonomous Privacy &amp; Compliance System</div>
      
      <a href="https://non-pursuit.streamlit.app/" target="_blank" rel="noopener noreferrer" class="launch-btn-main">
        <span>🚀</span>
        <span>Launch App: non-pursuit.streamlit.app</span>
      </a>
      
      <div class="cta-links-row">
        <a href="https://non-pursuit.streamlit.app/" target="_blank">🌐 Live Streamlit App</a>
        <span>·</span>
        <a href="https://youtu.be/V5qqb-L7ack" target="_blank">📹 YouTube Video Demo</a>
        <span>·</span>
        <a href="https://github.com/madfrantic/non-pursuit" target="_blank">💻 GitHub Repository</a>
        <span>·</span>
        <a href="javascript:showSlide(0)">⏮️ Replay Presentation</a>
      </div>
    </div>
  </section>

</div>

<!-- Chrome HUD -->
<div class="hud" id="hud-tl">
  <img src="assets/logo_shield.png" alt="Shield">
  <span>NON-PURSUIT // <b>SOVEREIGN ENGINE</b></span>
</div>

<div class="hud" id="hud-tr">
  <a href="https://non-pursuit.streamlit.app/" target="_blank" class="app-link">🚀 Open Live App</a>
  <span>SLIDE <b id="slide-no">01</b> / <span id="total-slides">21</span></span>
</div>

<div class="hud" id="hud-bl">DEMO DAY // CUNY LaGCC &amp; PURSUIT</div>

<div class="hud" id="hud-br">
  <div class="dots" id="dots"></div>
</div>

<button class="nav-arrow" id="prev-btn" aria-label="Previous Slide">‹</button>
<button class="nav-arrow" id="next-btn" aria-label="Next Slide">›</button>

<script>
(function(){
  var slides = document.querySelectorAll(".slide");
  var total = slides.length;
  var idx = 0;
  var dotsContainer = document.getElementById("dots");
  var slideNoEl = document.getElementById("slide-no");
  document.getElementById("total-slides").textContent = ("0" + total).slice(-2);

  // Generate dots
  slides.forEach(function(_, i){
    var dot = document.createElement("div");
    dot.className = "dot" + (i === 0 ? " on" : "");
    dot.title = "Slide " + (i + 1);
    dot.addEventListener("click", function(e){
      e.stopPropagation();
      showSlide(i);
    });
    dotsContainer.appendChild(dot);
  });

  window.showSlide = function(n){
    if(n < 0 || n >= total) return;
    slides[idx].classList.remove("active");
    idx = n;
    slides[idx].classList.add("active");
    slideNoEl.textContent = ("0" + (idx + 1)).slice(-2);
    document.querySelectorAll(".dot").forEach(function(d, i){
      d.classList.toggle("on", i === idx);
    });
  };

  document.getElementById("prev-btn").addEventListener("click", function(e){
    e.stopPropagation();
    showSlide(idx - 1);
  });
  document.getElementById("next-btn").addEventListener("click", function(e){
    e.stopPropagation();
    showSlide(idx + 1);
  });

  document.addEventListener("keydown", function(e){
    if(e.key === "ArrowRight" || e.key === " " || e.key === "PageDown"){
      showSlide(idx + 1);
    } else if(e.key === "ArrowLeft" || e.key === "PageUp"){
      showSlide(idx - 1);
    } else if(e.key === "Home"){
      showSlide(0);
    } else if(e.key === "End"){
      showSlide(total - 1);
    } else if(e.key.toLowerCase() === "f"){
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(function(){});
      } else {
        document.exitFullscreen().catch(function(){});
      }
    }
  });

  // Touch Swipe navigation
  var touchStartX = 0;
  document.addEventListener("touchstart", function(e){
    touchStartX = e.changedTouches[0].screenX;
  }, false);
  document.addEventListener("touchend", function(e){
    var touchEndX = e.changedTouches[0].screenX;
    var diff = touchStartX - touchEndX;
    if(Math.abs(diff) > 45){
      if(diff > 0) showSlide(idx + 1);
      else showSlide(idx - 1);
    }
  }, false);

  // Click on background advances
  document.getElementById("deck").addEventListener("click", function(e){
    if(e.target.tagName !== "A" && e.target.tagName !== "BUTTON" && e.target.tagName !== "IFRAME"){
      showSlide(idx + 1);
    }
  });
})();
</script>
</body>
</html>
"""

# Write out to all presentation destinations
destinations = [
    "docs/index.html",
    "docs/demo_day_final.html",
    "index.html",
    "demo_day_final.html"
]

for dst in destinations:
    with open(dst, "w", encoding="utf-8") as f:
        f.write(html_template)
    print(f"Wrote {dst}")

print("Successfully generated all presentation files!")
