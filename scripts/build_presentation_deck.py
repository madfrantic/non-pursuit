#!/usr/bin/env python3
import os

slides = [f"assets/slides/slide-{i:02d}.png" for i in range(1, 20)]

slide_sections = []
for idx, slide_path in enumerate(slides, 1):
    active_cls = " active" if idx == 1 else ""
    slide_sections.append(f"""  <!-- Slide {idx}: PDF Page {idx} -->
  <section class="slide{active_cls}" id="s{idx}">
    <div class="slide-img-box">
      <img src="{slide_path}" alt="Non-Pursuit Presentation Slide {idx}">
    </div>
  </section>""")

slides_html = "\n".join(slide_sections)

html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NON-PURSUIT — Digital Sovereignty & Autonomous Compliance</title>
<link rel="icon" href="assets/logo_shield.png">
<style>
:root {{
  --navy:      #0B1325;   
  --slate:     #152238;   
  --slate-hi:  #1D2E4A;
  --brass:     #D4AF37;   
  --brass-dim: #8A7328;
  --bone:      #E8E2D4;
  --bone-dim:  #9AA3B2;
  --evidence:  #FF3B30;   
  --ink:       #060A14;
  --serif: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
  --mono:  ui-monospace, "SF Mono", "Fira Code", "Courier New", monospace;
  --sans:  "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --ease: cubic-bezier(.22,.61,.36,1);
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{
  height: 100%; width: 100%;
  background: var(--ink);
  color: var(--bone);
  font-family: var(--sans);
  overflow: hidden;
  user-select: none;
}}
#deck {{
  position: fixed; inset: 0;
  background: radial-gradient(120% 90% at 50% 0%, #16233C 0%, var(--navy) 45%, var(--ink) 100%), var(--navy);
  display: flex; align-items: center; justify-content: center;
}}
.slide {{
  position: absolute; inset: 0;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 55px 35px 65px;
  opacity: 0; visibility: hidden;
  transform: translate3d(0, 14px, 0) scale(0.99);
  transition: opacity .45s var(--ease), transform .55s var(--ease), visibility 0s linear .55s;
  z-index: 1;
}}
.slide.active {{
  opacity: 1; visibility: visible;
  transform: translate3d(0, 0, 0) scale(1);
  transition: opacity .45s var(--ease), transform .55s var(--ease), visibility 0s linear 0s;
  z-index: 2;
}}

/* Slide image container for PDF slides */
.slide-img-box {{
  width: 100%; height: 100%;
  max-width: 1400px; max-height: 84vh;
  display: flex; align-items: center; justify-content: center;
  position: relative;
}}
.slide-img-box img {{
  max-width: 100%; max-height: 100%;
  width: auto; height: auto;
  object-fit: contain;
  border-radius: 4px;
  border: 1px solid rgba(212,175,55,.32);
  box-shadow: 0 14px 48px rgba(0,0,0,.8), 0 0 35px rgba(212,175,55,.1);
}}

/* Video Frame Slide */
.video-frame {{
  position: relative;
  width: min(1100px, 92vw);
  height: min(65vh, 620px);
  background: var(--ink);
  border: 1px solid rgba(212,175,55,.45);
  box-shadow: 0 16px 54px rgba(0,0,0,.85), 0 0 45px rgba(212,175,55,.12);
  border-radius: 4px;
  overflow: hidden;
}}
.video-frame iframe {{
  width: 100%; height: 100%;
  border: 0;
}}

/* Final Call To Action Slide */
.cta-wrap {{
  width: min(960px, 92vw);
  text-align: center;
  background: linear-gradient(180deg, rgba(21,34,56,.95), rgba(11,19,37,.98));
  border: 1px solid rgba(212,175,55,.4);
  border-radius: 6px;
  padding: clamp(32px, 5vh, 56px) clamp(24px, 4vw, 48px);
  box-shadow: 0 20px 60px rgba(0,0,0,.85), 0 0 50px rgba(212,175,55,.18);
}}
.cta-logo {{
  height: 84px; width: auto;
  margin-bottom: 20px;
  filter: drop-shadow(0 4px 18px rgba(212,175,55,.35));
}}
.cta-title {{
  font-family: var(--serif);
  font-size: clamp(32px, 4.5vw, 54px);
  color: var(--brass);
  line-height: 1.1;
  margin-bottom: 12px;
  text-shadow: 0 2px 0 rgba(0,0,0,.5), 0 0 40px rgba(212,175,55,.2);
}}
.cta-sub {{
  font-family: var(--mono);
  font-size: clamp(12px, 1.2vw, 15px);
  letter-spacing: .22em;
  text-transform: uppercase;
  color: var(--bone-dim);
  margin-bottom: 32px;
}}
.cta-btn-primary {{
  display: inline-flex; align-items: center; justify-content: center; gap: 12px;
  background: linear-gradient(180deg, #E6C65C 0%, #D4AF37 100%);
  color: #060A14;
  font-family: var(--mono);
  font-size: clamp(14px, 1.4vw, 18px);
  font-weight: 700;
  letter-spacing: .12em;
  text-transform: uppercase;
  padding: 16px 36px;
  border-radius: 3px;
  text-decoration: none;
  border: 1px solid #FFE484;
  box-shadow: 0 4px 24px rgba(212,175,55,.4), 0 0 12px rgba(212,175,55,.2);
  transition: all .25s ease;
  margin-bottom: 24px;
}}
.cta-btn-primary:hover {{
  transform: translateY(-2px);
  box-shadow: 0 6px 32px rgba(212,175,55,.6), 0 0 20px rgba(212,175,55,.4);
  background: #FFE484;
}}
.cta-links {{
  display: flex; align-items: center; justify-content: center; gap: 24px;
  font-family: var(--mono);
  font-size: 13px;
  letter-spacing: .1em;
  color: var(--bone-dim);
}}
.cta-links a {{
  color: var(--brass);
  text-decoration: none;
  transition: color .2s ease;
}}
.cta-links a:hover {{
  color: #FFE484;
  text-decoration: underline;
}}

/* Heads Up Display (HUD) Chrome */
.hud {{
  position: fixed; z-index: 40;
  font-family: var(--mono);
  font-size: 11px; letter-spacing: .24em;
  text-transform: uppercase;
  color: rgba(154,163,178,.75);
}}
#hud-tl {{ top: 18px; left: 28px; display: flex; align-items: center; gap: 12px; }}
#hud-tl img {{ height: 24px; width: auto; }}
#hud-tl b {{ color: var(--brass); }}
#hud-tr {{ top: 18px; right: 28px; display: flex; align-items: center; gap: 16px; }}
#hud-tr .app-link {{
  color: var(--brass);
  text-decoration: none;
  background: rgba(212,175,55,.12);
  border: 1px solid rgba(212,175,55,.35);
  padding: 5px 12px;
  border-radius: 2px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: .16em;
  transition: all .2s ease;
}}
#hud-tr .app-link:hover {{
  background: rgba(212,175,55,.25);
  border-color: var(--brass);
  color: #FFE484;
}}

#hud-bl {{ bottom: 18px; left: 28px; }}
#hud-br {{ bottom: 18px; right: 28px; display: flex; align-items: center; gap: 18px; }}
.dots {{ display: flex; gap: 5px; }}
.dot {{ width: 12px; height: 3px; background: rgba(154,163,178,.25); border-radius: 1px; cursor: pointer; transition: all .3s ease; }}
.dot.on {{ background: var(--brass); width: 22px; }}

/* Navigation buttons */
.nav-arrow {{
  position: fixed; top: 50%; transform: translateY(-50%);
  z-index: 50;
  background: rgba(11,19,37,.65);
  border: 1px solid rgba(212,175,55,.25);
  color: var(--brass);
  width: 44px; height: 56px;
  display: flex; align-items: center; justify-content: center;
  font-size: 20px;
  cursor: pointer;
  border-radius: 3px;
  transition: all .2s ease;
  backdrop-filter: blur(4px);
}}
.nav-arrow:hover {{
  background: rgba(21,34,56,.95);
  border-color: var(--brass);
  transform: translateY(-50%) scale(1.05);
}}
#prev-btn {{ left: 14px; }}
#next-btn {{ right: 14px; }}

@media (max-width: 768px) {{
  .nav-arrow {{ display: none; }}
  .hud {{ font-size: 9px; }}
  #hud-tl {{ left: 15px; top: 12px; }}
  #hud-tr {{ right: 15px; top: 12px; }}
  #hud-bl {{ left: 15px; bottom: 12px; }}
  #hud-br {{ right: 15px; bottom: 12px; }}
}}
</style>
</head>
<body>

<div id="deck">
{slides_html}

  <!-- Slide 20: Live Video Demo -->
  <section class="slide" id="s20">
    <div class="video-frame">
      <iframe src="https://www.youtube.com/embed/51bIU-S6hho?rel=0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>
    </div>
  </section>

  <!-- Slide 21: Live App Link / Call To Action -->
  <section class="slide" id="s21">
    <div class="cta-wrap">
      <img src="assets/logo_shield.png" alt="Non-Pursuit Shield" class="cta-logo">
      <h1 class="cta-title">non-pursuit. the sovereign engine</h1>
      <div class="cta-sub">Local-First Autonomous Privacy & Compliance System</div>
      <a href="https://non-pursuit.streamlit.app/" target="_blank" rel="noopener noreferrer" class="cta-btn-primary">
        🚀 Launch Live App: non-pursuit.streamlit.app
      </a>
      <div class="cta-links">
        <a href="https://non-pursuit.streamlit.app/" target="_blank">🌐 Web Version</a>
        <span>·</span>
        <a href="https://github.com/madfrantic/non-pursuit" target="_blank">💻 Source Code</a>
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

<div class="hud" id="hud-bl">DEMO DAY // CUNY LaGCC & PURSUIT</div>

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

  // Generate dots
  slides.forEach(function(_, i){{
    var dot = document.createElement("div");
    dot.className = "dot" + (i === 0 ? " on" : "");
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

  // Touch Swipe navigation
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

  // Click on background advances
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

with open("demo_day_final.html", "w") as f:
    f.write(html_content)

with open("index.html", "w") as f:
    f.write(html_content)

print(f"Successfully generated presentation with {len(slides)} PDF slides + Video + Launch App Screen!")
