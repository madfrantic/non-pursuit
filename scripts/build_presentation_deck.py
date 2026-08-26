#!/usr/bin/env python3
"""
Non-Pursuit Presentation Deck Generator
Projector-optimized, ultra-legible, high-contrast slides
Features Dual Side-by-Side Videos (Demo & Tutorial) on the Video Slide
"""
import os
import subprocess
import shutil

# Check if BIO_ME02.pdf or BIO_ME05.pdf is preferred
PDF_PATH = "/home/b0t/Documents/PURSUIT/BIO_ME02.pdf"
if not os.path.exists(PDF_PATH):
    PDF_PATH = "/home/b0t/Documents/PURSUIT/BIO_ME05.pdf"

os.makedirs("assets/slides", exist_ok=True)
os.makedirs("docs/assets/slides", exist_ok=True)

# Clean existing PNGs
for f in os.listdir("assets/slides"):
    if f.endswith(".png"):
        os.remove(os.path.join("assets/slides", f))
for f in os.listdir("docs/assets/slides"):
    if f.endswith(".png"):
        os.remove(os.path.join("docs/assets/slides", f))

# Render PDF pages 1-7 to PNG
subprocess.run(["pdftoppm", "-png", "-r", "150", "-l", "7", PDF_PATH, "assets/slides/slide"], check=True)

raw_files = sorted([f for f in os.listdir("assets/slides") if f.endswith(".png")])[:7]
for idx, f in enumerate(raw_files, 1):
    src = os.path.join("assets/slides", f)
    dst = os.path.join("assets/slides", f"slide-{idx:02d}.png")
    if src != dst:
        os.rename(src, dst)
    shutil.copy2(dst, os.path.join("docs/assets/slides", f"slide-{idx:02d}.png"))

# Remove any extra slide images beyond slide-07.png
for dir_path in ["assets/slides", "docs/assets/slides"]:
    for f in os.listdir(dir_path):
        if f.endswith(".png") and f.startswith("slide-"):
            try:
                num = int(f.replace("slide-", "").replace(".png", ""))
                if num > 7:
                    os.remove(os.path.join(dir_path, f))
            except ValueError:
                pass

num_pdf_slides = len(raw_files)
print(f"Extracted {num_pdf_slides} slides from {PDF_PATH} (7 core slides)")

for logo in ["assets/logo_shield.png", "assets/logo_wordmark.png"]:
    if os.path.exists(logo):
        shutil.copy2(logo, os.path.join("docs", logo))

video_slide_num = num_pdf_slides + 2  # Slide 09
app_slide_num = num_pdf_slides + 3    # Slide 10
total_deck_slides = app_slide_num      # 10 Total Slides

html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NON-PURSUIT — Digital Sovereignty & Autonomous Compliance</title>
<link rel="icon" href="assets/logo_shield.png">
<style>
:root {{
  --bg-dark:     #05080E;
  --bg-panel:    #0C101C;
  --bg-dossier:  #FFFFFF;
  --border-dark: #1E293B;
  --border-doss: #000000;
  --grid-line:   rgba(255,255,255,0.05);
  --grid-light:  rgba(0,0,0,0.05);
  --brass:       #D4AF37;
  --brass-light: #FDE047;
  --crimson:     #DC2626;
  --ink:         #0A0D14;
  --text-main:   #F8FAFC;
  --text-dim:    #94A3B8;
  --mono:        ui-monospace, "SF Mono", "Fira Code", "Courier New", monospace;
  --sans:        "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --ease:        cubic-bezier(.16, 1, .3, 1);
}}

* {{ box-sizing: border-box; margin: 0; padding: 0; }}

html, body {{
  height: 100%; width: 100%;
  background: var(--bg-dark);
  color: var(--text-main);
  font-family: var(--sans);
  overflow: hidden;
  user-select: none;
}}

#deck {{
  position: fixed; inset: 0;
  background-color: var(--bg-dark);
  background-image: 
    linear-gradient(var(--grid-line) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid-line) 1px, transparent 1px);
  background-size: 36px 36px;
  display: flex; align-items: center; justify-content: center;
}}

.slide {{
  position: absolute; inset: 0;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: clamp(35px, 4.5vh, 65px) clamp(20px, 3vw, 40px);
  opacity: 0; visibility: hidden;
  transform: translate3d(0, 10px, 0) scale(0.99);
  transition: opacity .35s var(--ease), transform .4s var(--ease), visibility 0s linear .4s;
  z-index: 1;
}}

.slide.active {{
  opacity: 1; visibility: visible;
  transform: translate3d(0, 0, 0) scale(1);
  transition: opacity .35s var(--ease), transform .4s var(--ease), visibility 0s linear 0s;
  z-index: 2;
}}

.slide-img-box {{
  width: 100%; height: 100%;
  max-width: 1440px; max-height: 86vh;
  display: flex; align-items: center; justify-content: center;
  position: relative;
}}

.slide-img-box img {{
  max-width: 100%; max-height: 100%;
  width: auto; height: auto;
  object-fit: contain;
  border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.15);
  box-shadow: 0 20px 60px rgba(0,0,0,0.9), 0 0 35px rgba(212,175,55,0.08);
}}

/* Projector-Optimized Dossier Card */
.dossier-card {{
  width: 100%;
  max-width: 1360px;
  max-height: 86vh;
  aspect-ratio: 16/9;
  background: var(--bg-dossier);
  color: var(--ink);
  border-radius: 4px;
  border: 3px solid var(--border-doss);
  box-shadow: 0 24px 70px rgba(0,0,0,0.95), 0 0 40px rgba(212,175,55,0.12);
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: clamp(28px, 4vh, 52px) clamp(32px, 4.5vw, 64px);
  background-image: 
    linear-gradient(var(--grid-light) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid-light) 1px, transparent 1px);
  background-size: 32px 32px;
}}

.dossier-header {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 3px solid #000;
  padding-bottom: clamp(14px, 2vh, 20px);
}}

.dossier-case-tag {{
  font-family: var(--mono);
  font-size: clamp(13px, 1.3vw, 17px);
  font-weight: 800;
  letter-spacing: 0.12em;
  line-height: 1.4;
  color: #000;
}}

.dossier-case-tag span {{
  display: block;
  font-size: clamp(11px, 1.05vw, 14px);
  color: #4B5563;
  font-weight: 600;
  margin-top: 2px;
}}

.stamp {{
  font-family: var(--mono);
  font-weight: 900;
  text-transform: uppercase;
  letter-spacing: 0.2em;
  padding: clamp(6px, 1vh, 10px) clamp(14px, 1.8vw, 24px);
  border: 4px solid var(--crimson);
  color: var(--crimson);
  border-radius: 4px;
  transform: rotate(-3deg);
  display: inline-block;
  font-size: clamp(14px, 1.4vw, 20px);
  box-shadow: inset 0 0 0 1.5px var(--crimson);
  background: rgba(220,38,38,0.04);
}}

.stamp.green {{
  border-color: #059669;
  color: #059669;
  box-shadow: inset 0 0 0 1.5px #059669;
  transform: rotate(2deg);
  background: rgba(5,150,105,0.04);
}}

.intro-hero {{
  margin: clamp(16px, 2.5vh, 28px) 0;
}}

.intro-name {{
  font-size: clamp(38px, 4.8vw, 68px);
  font-weight: 900;
  letter-spacing: -0.03em;
  line-height: 1.05;
  color: #000;
  margin-bottom: 6px;
}}

.intro-project {{
  font-family: var(--mono);
  font-size: clamp(18px, 2vw, 28px);
  font-weight: 800;
  color: #1F2937;
  letter-spacing: 0.04em;
  display: flex;
  align-items: center;
  gap: 12px;
}}

.intro-project span.accent {{
  background: #000;
  color: #FFF;
  padding: 2px 10px;
  border-radius: 2px;
}}

.intro-cards-row {{
  display: grid;
  grid-template-columns: 1.6fr 1fr;
  gap: clamp(16px, 2.5vw, 32px);
  align-items: stretch;
}}

.intro-points-box {{
  background: #FFF;
  border: 2px solid #000;
  padding: clamp(18px, 2.5vh, 28px) clamp(20px, 2.5vw, 32px);
  box-shadow: 6px 6px 0px #000;
  display: flex;
  flex-direction: column;
  justify-content: space-around;
  gap: 14px;
}}

.point-item {{
  display: flex;
  align-items: flex-start;
  gap: 14px;
}}

.point-icon {{
  font-size: clamp(20px, 2vw, 28px);
  line-height: 1.2;
}}

.point-text {{
  font-size: clamp(15px, 1.5vw, 21px);
  line-height: 1.35;
  color: #111;
  font-weight: 500;
}}

.point-text strong {{
  font-weight: 800;
  color: #000;
}}

.intro-stats-col {{
  display: flex;
  flex-direction: column;
  gap: 12px;
  justify-content: space-between;
}}

.stat-box-large {{
  background: #000;
  color: #FFF;
  padding: clamp(14px, 2vh, 22px);
  border-radius: 3px;
  font-family: var(--mono);
  display: flex;
  flex-direction: column;
  justify-content: center;
  flex: 1;
}}

.stat-box-large .val {{
  font-size: clamp(26px, 3vw, 42px);
  font-weight: 900;
  color: var(--brass-light);
  line-height: 1;
  margin-bottom: 4px;
}}

.stat-box-large .lbl {{
  font-size: clamp(11px, 1.1vw, 15px);
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #D1D5DB;
}}

.dossier-footer {{
  border-top: 2px solid #000;
  padding-top: clamp(10px, 1.5vh, 16px);
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-family: var(--mono);
  font-size: clamp(12px, 1.15vw, 15px);
  font-weight: 700;
  color: #374151;
  letter-spacing: 0.08em;
}}

/* Dual Video Showcase Layout (Side-by-Side on the Video Slide) */
.dual-video-wrapper {{
  width: 100%;
  max-width: 1440px;
  height: min(84vh, 720px);
  display: flex;
  flex-direction: column;
  gap: 12px;
}}

.dual-video-header {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #0C1222;
  border: 2px solid #1E293B;
  border-radius: 4px;
  padding: 10px 20px;
  font-family: var(--mono);
  font-size: clamp(12px, 1.2vw, 15px);
  font-weight: 700;
  color: var(--brass-light);
}}

.dual-video-grid {{
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  min-height: 0;
}}

.video-box-card {{
  background: #080C16;
  border: 2.5px solid #1E293B;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 16px 50px rgba(0,0,0,0.9), 0 0 30px rgba(212,175,55,0.1);
}}

.video-box-bar {{
  background: #0F172A;
  border-bottom: 1.5px solid #1E293B;
  padding: 8px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-family: var(--mono);
  font-size: clamp(11px, 1.1vw, 14px);
  font-weight: 800;
}}

.video-box-bar .tag-demo {{
  color: var(--brass-light);
  display: flex;
  align-items: center;
  gap: 8px;
}}

.video-box-bar .tag-tutorial {{
  color: #38BDF8;
  display: flex;
  align-items: center;
  gap: 8px;
}}

.video-box-bar .ext-link {{
  color: #FFF;
  text-decoration: none;
  background: #1E293B;
  border: 1px solid #334155;
  padding: 4px 10px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 700;
  transition: all 0.2s ease;
}}

.video-box-bar .ext-link:hover {{
  background: var(--crimson);
  border-color: var(--crimson);
}}

.video-box-frame {{
  flex: 1;
  position: relative;
  background: #000;
}}

.video-box-frame iframe {{
  width: 100%;
  height: 100%;
  border: none;
}}

/* Call to Action Slide (Launch App) */
.cta-card {{
  width: 100%;
  max-width: 1200px;
  max-height: 86vh;
  background: var(--bg-dossier);
  color: var(--ink);
  border: 3px solid #000;
  border-radius: 4px;
  box-shadow: 0 24px 70px rgba(0,0,0,0.95), 0 0 50px rgba(212,175,55,0.18);
  padding: clamp(36px, 5vh, 64px) clamp(28px, 4.5vw, 64px);
  text-align: center;
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background-image: 
    linear-gradient(var(--grid-light) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid-light) 1px, transparent 1px);
  background-size: 32px 32px;
}}

.cta-title {{
  font-size: clamp(34px, 4.8vw, 64px);
  font-weight: 900;
  letter-spacing: -0.03em;
  line-height: 1.08;
  color: #000;
  margin: 12px 0 8px;
}}

.cta-subtitle {{
  font-family: var(--mono);
  font-size: clamp(14px, 1.5vw, 20px);
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #4B5563;
  margin-bottom: clamp(24px, 4vh, 40px);
  font-weight: 700;
}}

.launch-btn-main {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  background: #000;
  color: #FFF;
  font-family: var(--mono);
  font-size: clamp(18px, 2.2vw, 28px);
  font-weight: 900;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: clamp(20px, 2.8vh, 30px) clamp(36px, 5vw, 60px);
  border-radius: 4px;
  text-decoration: none;
  border: 3px solid #000;
  box-shadow: 8px 8px 0px var(--crimson);
  transition: all 0.2s ease;
  margin-bottom: clamp(24px, 3.5vh, 36px);
}}

.launch-btn-main:hover {{
  transform: translate(-3px, -3px);
  box-shadow: 12px 12px 0px var(--crimson);
  background: #111;
  color: var(--brass-light);
}}

.launch-btn-main:active {{
  transform: translate(2px, 2px);
  box-shadow: 4px 4px 0px var(--crimson);
}}

.cta-links-row {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: clamp(14px, 2vw, 28px);
  font-family: var(--mono);
  font-size: clamp(13px, 1.3vw, 17px);
  font-weight: 700;
  letter-spacing: 0.06em;
  color: #374151;
}}

.cta-links-row a {{
  color: #000;
  text-decoration: none;
  border-bottom: 2.5px solid #000;
  padding-bottom: 2px;
  transition: all 0.2s ease;
}}

.cta-links-row a:hover {{
  color: var(--crimson);
  border-color: var(--crimson);
}}

/* Chrome HUD */
.hud {{
  position: fixed; z-index: 40;
  font-family: var(--mono);
  font-size: clamp(11px, 1.1vw, 14px);
  letter-spacing: .2em;
  text-transform: uppercase;
  color: rgba(241,245,249,0.85);
  font-weight: 700;
}}

#hud-tl {{ top: 16px; left: 24px; display: flex; align-items: center; gap: 12px; }}
#hud-tl img {{ height: 28px; width: auto; filter: drop-shadow(0 2px 8px rgba(0,0,0,0.8)); }}
#hud-tl b {{ color: var(--brass-light); }}

#hud-tr {{ top: 16px; right: 24px; display: flex; align-items: center; gap: 18px; }}
#hud-tr .app-link {{
  color: #000;
  text-decoration: none;
  background: var(--brass);
  border: 1.5px solid var(--brass-light);
  padding: 6px 16px;
  border-radius: 3px;
  font-size: clamp(11px, 1.1vw, 13px);
  font-weight: 900;
  letter-spacing: .12em;
  transition: all .2s ease;
  box-shadow: 0 2px 10px rgba(212,175,55,0.4);
}}

#hud-tr .app-link:hover {{
  background: var(--brass-light);
  transform: translateY(-1px);
}}

#hud-bl {{ bottom: 16px; left: 24px; color: var(--text-dim); }}
#hud-br {{ bottom: 16px; right: 24px; display: flex; align-items: center; gap: 16px; }}

.dots {{ display: flex; gap: 6px; }}
.dot {{ width: 12px; height: 5px; background: rgba(255,255,255,0.25); border-radius: 2px; cursor: pointer; transition: all .25s ease; }}
.dot.on {{ background: var(--brass-light); width: 26px; }}

/* Navigation Buttons */
.nav-arrow {{
  position: fixed; top: 50%; transform: translateY(-50%);
  z-index: 50;
  background: rgba(15,23,42,0.9);
  border: 1.5px solid rgba(255,255,255,0.2);
  color: var(--brass-light);
  width: 52px; height: 68px;
  display: flex; align-items: center; justify-content: center;
  font-size: 26px;
  font-weight: 800;
  cursor: pointer;
  border-radius: 4px;
  transition: all .2s ease;
  backdrop-filter: blur(8px);
}}

.nav-arrow:hover {{
  background: #1E293B;
  border-color: var(--brass-light);
  transform: translateY(-50%) scale(1.06);
}}

#prev-btn {{ left: 16px; }}
#next-btn {{ right: 16px; }}

@media (max-width: 900px) {{
  .dual-video-grid {{ grid-template-columns: 1fr; }}
  .nav-arrow {{ display: none; }}
  .hud {{ font-size: 9px; }}
  #hud-tl {{ left: 12px; top: 10px; }}
  #hud-tr {{ right: 12px; top: 10px; }}
  #hud-bl {{ left: 12px; bottom: 10px; }}
  #hud-br {{ right: 12px; bottom: 10px; }}
  .intro-cards-row {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>

<div id="deck">

  <!-- Slide 01: About Me / Intro -->
  <section class="slide active" id="s1">
    <div class="dossier-card">
      <div class="dossier-header">
        <div class="dossier-case-tag">
          CASE NO. 2026-CCPA-08 // BUILDER DOSSIER
          <span>PURSUIT x CUNY LaGUARDIA COMMUNITY COLLEGE — AI FUNDAMENTALS</span>
        </div>
        <div class="stamp">CONFIDENTIAL SEALED</div>
      </div>
      
      <div class="intro-hero">
        <h1 class="intro-name">John Cuentas</h1>
        <div class="intro-project">
          <span>PROJECT:</span>
          <span class="accent">NON-PURSUIT</span>
          <span>&gt; THE SOVEREIGN ENGINE</span>
        </div>
      </div>
      
      <div class="intro-cards-row">
        <div class="intro-points-box">
          <div class="point-item">
            <div class="point-icon">🎯</div>
            <div class="point-text">
              <strong>Objective:</strong> Build local-first, zero-knowledge tools to permanently wipe public data footprints.
            </div>
          </div>
          <div class="point-item">
            <div class="point-icon">⚡</div>
            <div class="point-text">
              <strong>The Problem:</strong> Break the recurring commercial privacy subscription loop ($15/mo forever).
            </div>
          </div>
          <div class="point-item">
            <div class="point-icon">🛡️</div>
            <div class="point-text">
              <strong>The Engine:</strong> Automated statutory deletion demands (CCPA/GDPR) running entirely on your local machine.
            </div>
          </div>
        </div>
        
        <div class="intro-stats-col">
          <div class="stat-box-large">
            <div class="val">100%</div>
            <div class="lbl">Zero-Knowledge Local Vault</div>
          </div>
          <div class="stat-box-large">
            <div class="val">1,070+</div>
            <div class="lbl">Automated Test Verifications</div>
          </div>
        </div>
      </div>
      
      <div class="dossier-footer">
        <div>SUBJECT: JOHN CUENTAS // DEMO DAY 2026</div>
        <div>DEMO DURATION: 3:00</div>
        <div>non-pursuit.streamlit.app</div>
      </div>
    </div>
  </section>

  <!-- Slides 02-{num_pdf_slides + 1:02d}: BIO_ME02.pdf Pages 01-{num_pdf_slides:02d} -->
"""

slide_items = []
for i in range(1, num_pdf_slides + 1):
    slide_num = i + 1
    slide_path = f"assets/slides/slide-{i:02d}.png"
    slide_items.append(f"""  <!-- Slide {slide_num:02d}: PDF Slide Page {i:02d} -->
  <section class="slide" id="s{slide_num}">
    <div class="slide-img-box">
      <img src="{slide_path}" alt="Non-Pursuit Presentation Slide {i}">
    </div>
  </section>""")

html_template += "\n".join(slide_items)

html_template += f"""

  <!-- Slide {video_slide_num:02d}: Dual Video Showcase (Demo Video + App Tutorial) -->
  <section class="slide" id="s{video_slide_num}">
    <div class="dual-video-wrapper">
      <div class="dual-video-header">
        <div>📹 NON-PURSUIT // VIDEO SHOWCASE &amp; TUTORIAL</div>
        <div>OFFICIAL DEMO + FULL APP WALKTHROUGH</div>
      </div>
      
      <div class="dual-video-grid">
        <!-- Video 1: Official Demo Video -->
        <div class="video-box-card">
          <div class="video-box-bar">
            <div class="tag-demo">
              <span>▶</span>
              <span>1. DEMO VIDEO (3 MIN)</span>
            </div>
            <a href="https://youtu.be/V5qqb-L7ack" target="_blank" rel="noopener noreferrer" class="ext-link">
              ↗ youtu.be/V5qqb-L7ack
            </a>
          </div>
          <div class="video-box-frame">
            <iframe 
              src="https://www.youtube.com/embed/V5qqb-L7ack?rel=0" 
              title="Non-Pursuit Official Demo Video"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" 
              allowfullscreen>
            </iframe>
          </div>
        </div>

        <!-- Video 2: App Tutorial & Walkthrough -->
        <div class="video-box-card">
          <div class="video-box-bar">
            <div class="tag-tutorial">
              <span>🎓</span>
              <span>2. APP TUTORIAL &amp; WALKTHROUGH</span>
            </div>
            <a href="https://youtu.be/hFP6orcBl3I" target="_blank" rel="noopener noreferrer" class="ext-link">
              ↗ youtu.be/hFP6orcBl3I
            </a>
          </div>
          <div class="video-box-frame">
            <iframe 
              src="https://www.youtube.com/embed/hFP6orcBl3I?rel=0" 
              title="Non-Pursuit App Tutorial & Walkthrough"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" 
              allowfullscreen>
            </iframe>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- Slide {app_slide_num:02d}: Live Streamlit App Launch & Call To Action -->
  <section class="slide" id="s{app_slide_num}">
    <div class="cta-card">
      <div class="stamp green">STATUS: LIVE IN PRODUCTION</div>
      <h1 class="cta-title">non-pursuit. the sovereign engine</h1>
      <div class="cta-subtitle">Local-First Autonomous Privacy &amp; Compliance System</div>
      
      <a href="https://non-pursuit.streamlit.app/" target="_blank" rel="noopener noreferrer" class="launch-btn-main">
        <span>🚀</span>
        <span>Launch App: non-pursuit.streamlit.app</span>
      </a>
      
      <div class="cta-links-row">
        <a href="https://non-pursuit.streamlit.app/" target="_blank">🌐 Live Streamlit App</a>
        <span>·</span>
        <a href="https://youtu.be/V5qqb-L7ack" target="_blank">📹 Demo Video</a>
        <span>·</span>
        <a href="https://youtu.be/hFP6orcBl3I" target="_blank">🎓 App Tutorial</a>
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
  <span>SLIDE <b id="slide-no">01</b> / <span id="total-slides">{total_deck_slides:02d}</span></span>
</div>

<div class="hud" id="hud-bl">DEMO DAY // CUNY LaGCC &amp; PURSUIT</div>

<div class="hud" id="hud-br">
  <div class="dots" id="dots"></div>
</div>

<button class="nav-arrow" id="prev-btn" aria-label="Previous Slide">‹</button>
<button class="nav-arrow" id="next-btn" aria-label="Next Slide">›</button>

<script>
(function(){{
  var slides = document.querySelectorAll(".slide");
  var total = slides.length;
  var idx = 0;
  var dotsContainer = document.getElementById("dots");
  var slideNoEl = document.getElementById("slide-no");
  document.getElementById("total-slides").textContent = ("0" + total).slice(-2);

  slides.forEach(function(_, i){{
    var dot = document.createElement("div");
    dot.className = "dot" + (i === 0 ? " on" : "");
    dot.title = "Slide " + (i + 1);
    dot.addEventListener("click", function(e){{
      e.stopPropagation();
      showSlide(i);
    }});
    dotsContainer.appendChild(dot);
  }});

  window.showSlide = function(n){{
    if(n < 0 || n >= total) return;
    slides[idx].classList.remove("active");
    idx = n;
    slides[idx].classList.add("active");
    slideNoEl.textContent = ("0" + (idx + 1)).slice(-2);
    document.querySelectorAll(".dot").forEach(function(d, i){{
      d.classList.toggle("on", i === idx);
    }});
  }};

  document.getElementById("prev-btn").addEventListener("click", function(e){{
    e.stopPropagation();
    showSlide(idx - 1);
  }});
  document.getElementById("next-btn").addEventListener("click", function(e){{
    e.stopPropagation();
    showSlide(idx + 1);
  }});

  document.addEventListener("keydown", function(e){{
    if(e.key === "ArrowRight" || e.key === " " || e.key === "PageDown"){{
      showSlide(idx + 1);
    }} else if(e.key === "ArrowLeft" || e.key === "PageUp"){{
      showSlide(idx - 1);
    }} else if(e.key === "Home"){{
      showSlide(0);
    }} else if(e.key === "End"){{
      showSlide(total - 1);
    }} else if(e.key.toLowerCase() === "f"){{
      if (!document.fullscreenElement) {{
        document.documentElement.requestFullscreen().catch(function(){{}});
      }} else {{
        document.exitFullscreen().catch(function(){{}});
      }}
    }}
  }});

  var touchStartX = 0;
  document.addEventListener("touchstart", function(e){{
    touchStartX = e.changedTouches[0].screenX;
  }}, false);
  document.addEventListener("touchend", function(e){{
    var touchEndX = e.changedTouches[0].screenX;
    var diff = touchStartX - touchEndX;
    if(Math.abs(diff) > 45){{
      if(diff > 0) showSlide(idx + 1);
      else showSlide(idx - 1);
    }}
  }}, false);

  document.getElementById("deck").addEventListener("click", function(e){{
    if(e.target.tagName !== "A" && e.target.tagName !== "BUTTON" && e.target.tagName !== "IFRAME"){{
      showSlide(idx + 1);
    }}
  }});
}})();
</script>
</body>
</html>
"""

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

print(f"Successfully generated dual-video deck with {total_deck_slides} total slides!")
