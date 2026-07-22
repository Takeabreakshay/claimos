/* =============================================================================
   ClaimOS — customer self-service claim journey (deliverable #7).
   A guided FNOL that runs on the SAME live engine as the insurer console:
   policy lookup -> incident -> photos (live vision) -> submit -> instant triage.
   The outcome is shown in the customer's language, including the "advise-withdraw"
   moment (don't claim a dent that costs you more in NCB than it pays out).
   ============================================================================= */
"use strict";

const C = {
  step: "policy",
  policy: null,
  incident: { type: null, severity: null, date: today(), desc: "", amount: "" },
  claimId: null,
  photos: [],
  result: null,
  withdrawChoice: null,
};

const STEPS = ["policy", "incident", "photos", "review"];
const $ = (id) => document.getElementById(id);
const app = () => $("app");
function today() { const d = new Date(); return d.toISOString().slice(0, 10); }
const money = (n) => "₹" + Math.round(Number(n) || 0).toLocaleString("en-IN");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.text()).slice(0, 200) || r.status);
  return r.json();
}

/* ---------- incident taxonomy ---------- */
const INCIDENTS = [
  { k: "collision", ic: "💥", t: "Collision", d: "Hit something / someone hit me", claim_type: "OD" },
  { k: "glass", ic: "🪟", t: "Glass / windshield", d: "Cracked or shattered glass", claim_type: "OD", severity: "minor" },
  { k: "theft", ic: "🔓", t: "Theft", d: "Vehicle or parts stolen", claim_type: "theft_total", severity: "total", fir: true },
  { k: "fire", ic: "🔥", t: "Fire", d: "Fire or short-circuit damage", claim_type: "OD", severity: "severe" },
  { k: "third_party", ic: "🚗", t: "Third-party", d: "I damaged someone / injury", claim_type: "TP", fir: true },
  { k: "natural", ic: "🌧️", t: "Natural / flood", d: "Flood, tree, storm, quake", claim_type: "OD" },
];
const SEVERITIES = [
  { k: "minor", ic: "🩹", t: "Scratches & dents", d: "Cosmetic, car drives fine" },
  { k: "moderate", ic: "🔧", t: "Significant damage", d: "Panels / lights, still drivable" },
  { k: "severe", ic: "🛑", t: "Severe / undrivable", d: "Major impact, can't drive it" },
];
const ANGLES = ["Front-left 45°", "Front-right 45°", "Damage close-up", "Number plate"];

/* ---------- stepper ---------- */
function renderStepper() {
  const idx = STEPS.indexOf(C.step);
  const on = C.step === "result" || C.step === "track" ? STEPS.length : idx;
  $("stepper").innerHTML = STEPS.map((_, i) =>
    `<div class="seg ${i < on ? "done" : i === on ? "now" : ""}"></div>`).join("");
}

/* ---------- router ---------- */
function render() {
  renderStepper();
  const el = app();
  el.scrollTo?.(0, 0); window.scrollTo(0, 0);
  ({ policy: stepPolicy, incident: stepIncident, photos: stepPhotos,
     review: stepReview, result: stepResult, track: stepTrack }[C.step] || stepPolicy)(el);
}

/* ============================ STEP 1 · POLICY ============================ */
function stepPolicy(el) {
  el.innerHTML = `
    <div class="cx-card">
      <div class="cx-eyebrow">Step 1 · Your policy</div>
      <h1 class="cx-h">Let's find your policy</h1>
      <p class="cx-lede">Enter your motor policy number and we'll pull up your cover. No paperwork.</p>
      <div class="cx-field">
        <label>Policy number</label>
        <input class="cx-input" id="pol" placeholder="e.g. BAJAJ-MT-4415" value="${esc(C.policy?.policy_id || "")}" autocomplete="off">
      </div>
      <div class="cx-chips">
        <span style="font-size:11.5px;color:var(--slate-2);align-self:center">Try a demo:</span>
        <span class="cx-chip" data-p="BAJAJ-MT-4415">BAJAJ-MT-4415</span>
        <span class="cx-chip" data-p="ACKO-CAR-9920">ACKO-CAR-9920</span>
        <span class="cx-chip" data-p="TATA-AIG-3071">TATA-AIG-3071</span>
      </div>
      <div id="polResult"></div>
      <div class="cx-actions">
        <button class="cx-btn primary" id="findBtn">Find my policy</button>
      </div>
    </div>`;
  $("findBtn").onclick = () => findPolicy($("pol").value.trim());
  $("pol").addEventListener("keydown", e => { if (e.key === "Enter") findPolicy($("pol").value.trim()); });
  el.querySelectorAll(".cx-chip").forEach(c =>
    c.onclick = () => { $("pol").value = c.dataset.p; findPolicy(c.dataset.p); });
  if (C.policy) showPolicy(C.policy);
}

async function findPolicy(pid) {
  if (!pid) return;
  const out = $("polResult");
  out.innerHTML = `<div class="cx-note"><span class="cx-spin"></span> Looking up your policy…</div>`;
  try {
    const p = await api("/api/policies/" + encodeURIComponent(pid));
    C.policy = p;
    showPolicy(p);
  } catch (e) {
    out.innerHTML = `<div class="cx-note warn"><span class="s">!</span> Couldn't find that policy. Check the number and try again.</div>`;
  }
}

function showPolicy(p) {
  const addonName = { zero_depreciation: "Zero-dep", engine_protection: "Engine Protect",
    consumables: "Consumables", return_to_invoice: "Return to Invoice", roadside_assistance: "Roadside" };
  const badges = [`${p.product_type === "comprehensive" ? "Comprehensive" : p.product_type}`,
    `NCB ${p.ncb_percent}%`].map(b => `<span class="cx-badge">${esc(b)}</span>`).join("")
    + (p.add_ons || []).map(a => `<span class="cx-badge on">${esc(addonName[a] || a)}</span>`).join("");
  $("polResult").innerHTML = `
    <div class="cx-policy">
      <div class="veh">🚙</div>
      <div class="info">
        <div class="m">${esc(p.make)} ${esc(p.model)}</div>
        <div class="r">${esc(p.registration_no)} · ${p.cubic_capacity}cc · ${esc(p.fuel_type)} · ${p.vehicle_age_years} yr</div>
      </div>
    </div>
    <div style="margin-top:12px">
      <div class="cx-kv"><span class="k">Insured value (IDV)</span><span class="v">${money(p.idv)}</span></div>
      <div class="cx-kv"><span class="k">Cover valid till</span><span class="v">${esc((p.period_to || "").slice(0, 10))}</span></div>
    </div>
    <div class="cx-badges">${badges}</div>`;
  const b = $("findBtn"); b.textContent = "This is my car — continue"; b.onclick = () => go("incident");
}

/* ============================ STEP 2 · INCIDENT ============================ */
function stepIncident(el) {
  const I = C.incident;
  const isTheft = I.type && INCIDENTS.find(x => x.k === I.type)?.severity === "total";
  el.innerHTML = `
    <div class="cx-card">
      <div class="cx-eyebrow">Step 2 · What happened</div>
      <h1 class="cx-h">Tell us about the incident</h1>
      <p class="cx-lede">A few quick details. This shapes how fast we can settle.</p>
      <div class="cx-grid">
        ${INCIDENTS.map(x => `<button class="cx-opt ${I.type === x.k ? "sel" : ""}" data-inc="${x.k}">
          <span class="ic">${x.ic}</span><span class="t">${x.t}</span><span class="d">${x.d}</span></button>`).join("")}
      </div>
      <div id="sevBlock" style="${isTheft ? "display:none" : ""}">
        <div class="cx-field" style="margin-top:20px"><label>How bad is the damage?</label></div>
        <div class="cx-grid" style="margin-top:0">
          ${SEVERITIES.map(s => `<button class="cx-opt ${I.severity === s.k ? "sel" : ""}" data-sev="${s.k}">
            <span class="ic">${s.ic}</span><span class="t">${s.t}</span><span class="d">${s.d}</span></button>`).join("")}
        </div>
      </div>
      <div class="cx-field"><label>When did it happen?</label>
        <input class="cx-input" type="date" id="incDate" value="${esc(I.date)}" max="${today()}"></div>
      <div class="cx-field"><label>Briefly, what happened? <span style="color:var(--slate-2)">(optional)</span></label>
        <textarea class="cx-area" id="incDesc" placeholder="e.g. Someone reversed into my parked car in the mall lot.">${esc(I.desc)}</textarea></div>
      <div class="cx-field"><label>Approx. repair estimate, if you have a quote <span style="color:var(--slate-2)">(optional)</span></label>
        <input class="cx-input" id="incAmt" inputmode="numeric" placeholder="e.g. 28000" value="${esc(I.amount)}"></div>
      <div class="cx-actions">
        <button class="cx-btn ghost" onclick="CX.go('policy')">Back</button>
        <button class="cx-btn primary" id="incNext" disabled>Continue</button>
      </div>
    </div>`;

  const refresh = () => {
    const inc = INCIDENTS.find(x => x.k === I.type);
    const needSev = inc && inc.severity !== "total";
    $("sevBlock").style.display = needSev ? "" : "none";
    $("incNext").disabled = !(I.type && (!needSev || I.severity));
  };
  el.querySelectorAll("[data-inc]").forEach(b => b.onclick = () => {
    I.type = b.dataset.inc;
    const inc = INCIDENTS.find(x => x.k === I.type);
    if (inc.severity) I.severity = inc.severity;
    stepIncident(el);
  });
  el.querySelectorAll("[data-sev]").forEach(b => b.onclick = () => { I.severity = b.dataset.sev; stepIncident(el); });
  $("incDate").onchange = e => I.date = e.target.value;
  $("incDesc").oninput = e => I.desc = e.target.value;
  $("incAmt").oninput = e => I.amount = e.target.value.replace(/[^\d]/g, "");
  $("incNext").onclick = createAndGoPhotos;
  refresh();
}

async function createAndGoPhotos() {
  const btn = $("incNext"); btn.disabled = true; btn.innerHTML = `<span class="cx-spin"></span> Opening your claim…`;
  const p = C.policy, I = C.incident;
  const inc = INCIDENTS.find(x => x.k === I.type);
  const payload = {
    policy_id: p.policy_id, customer_id: "CUST-" + p.policy_id,
    claim_type: inc.claim_type, incident_severity: I.severity || "minor",
    incident_date: I.date + "T10:00:00", incident_description: I.desc,
    claim_amount: Number(I.amount) || 0,
    fir_filed: !!inc.fir, injury_hint: inc.k === "third_party",
    // policy attributes -> feed the coverage matrix + rate card
    make: p.make, model: p.model, segment: p.segment, cubic_capacity: p.cubic_capacity,
    vehicle_type: p.vehicle_type, idv: p.idv, vehicle_age_years: p.vehicle_age_years,
    product_type: p.product_type, period_from: p.period_from, period_to: p.period_to,
    add_ons: p.add_ons, voluntary_excess: p.voluntary_excess, claim_free_years: p.claim_free_years,
    od_premium_next_year: p.od_premium_next_year, city_tier: p.city_tier, geo: p.geo,
    is_ev: p.is_ev, usage_class: p.usage_class, garage_type: "network",
  };
  try {
    const r = await api("/api/claims", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    C.claimId = r.claim_id;
    go("photos");
  } catch (e) {
    btn.disabled = false; btn.textContent = "Continue";
    alert("Could not open the claim: " + e.message);
  }
}

/* ============================ STEP 3 · PHOTOS ============================ */
function stepPhotos(el) {
  el.innerHTML = `
    <div class="cx-card">
      <div class="cx-eyebrow">Step 3 · Photos of the damage</div>
      <h1 class="cx-h">Show us the damage</h1>
      <p class="cx-lede">Clear, well-lit photos let us settle faster. Aim for these angles:</p>
      <div class="cx-angles" id="angles">
        ${ANGLES.map((a, i) => `<span class="cx-angle ${i < C.photos.length ? "got" : ""}">${i < C.photos.length ? "✓" : "○"} ${esc(a)}</span>`).join("")}
      </div>
      <div class="cx-drop" id="drop"><b>Tap to add photos</b><br><span style="font-size:12px">or drag &amp; drop · analysed instantly</span></div>
      <input type="file" id="file" accept="image/*" multiple style="display:none">
      <div id="shots">${C.photos.map(photoRow).join("")}</div>
      <div class="cx-actions">
        <button class="cx-btn ghost" onclick="CX.go('incident')">Back</button>
        <button class="cx-btn primary" id="phNext" ${C.photos.length ? "" : "disabled"}>${C.photos.length ? "Review my claim" : "Add a photo to continue"}</button>
      </div>
    </div>`;
  const drop = $("drop"), file = $("file");
  drop.onclick = () => file.click();
  file.onchange = () => uploadPhotos(file.files);
  ["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", e => uploadPhotos(e.dataTransfer.files));
  $("phNext").onclick = () => go("review");
}

function photoRow(p) {
  const dmg = p.damage && p.damage.severity
    ? `AI sees: <b>${esc(p.damage.severity)}</b>${p.damage.damaged_parts?.length ? " · " + esc(p.damage.damaged_parts.slice(0, 3).join(", ")) : ""}`
    : (p.is_blurry ? "Blurry — a retake would help" : "Analysed");
  const q = Math.round((p.quality_score || 0) * 100);
  return `<div class="cx-photo">
    <img class="th" src="${p.thumb || ""}" alt="">
    <div class="pi">
      <div class="pn">${esc(p.filename)}</div>
      <div class="pm">${p.reuse_verdict === "reused" ? "⚠ This image was already used on another claim" : dmg}</div>
      <div class="cx-bar-q"><i style="width:${q}%;background:${q < 45 ? "var(--warn)" : "var(--good)"}"></i></div>
    </div>
  </div>`;
}

async function uploadPhotos(files) {
  if (!files || !files.length) return;
  const shots = $("shots");
  for (const f of files) {
    const thumb = await fileThumb(f);
    const pending = document.createElement("div");
    pending.className = "cx-photo";
    pending.innerHTML = `<img class="th" src="${thumb}"><div class="pi"><div class="pn">${esc(f.name)}</div>
      <div class="pm"><span class="cx-spin"></span> Analysing…</div></div>`;
    shots.appendChild(pending);
    try {
      const fd = new FormData(); fd.append("file", f); fd.append("angle", ANGLES[C.photos.length] || "wide");
      const r = await api(`/api/claims/${C.claimId}/photos`, { method: "POST", body: fd });
      r.thumb = thumb;
      C.photos.push(r);
    } catch (e) {
      pending.querySelector(".pm").innerHTML = `<span style="color:var(--bad)">Couldn't analyse — try another photo</span>`;
      continue;
    }
    stepPhotos(app());  // re-render to refresh angle checklist + next button
  }
}

function fileThumb(file) {
  return new Promise(res => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(file); });
}

/* ============================ STEP 4 · REVIEW ============================ */
function stepReview(el) {
  const p = C.policy, I = C.incident;
  const inc = INCIDENTS.find(x => x.k === I.type);
  const worst = C.photos.map(x => x.damage?.severity).filter(Boolean);
  el.innerHTML = `
    <div class="cx-card">
      <div class="cx-eyebrow">Step 4 · Review &amp; submit</div>
      <h1 class="cx-h">Does this look right?</h1>
      <div style="margin-top:14px">
        <div class="cx-kv"><span class="k">Vehicle</span><span class="v">${esc(p.make)} ${esc(p.model)}</span></div>
        <div class="cx-kv"><span class="k">Incident</span><span class="v">${esc(inc.t)}</span></div>
        <div class="cx-kv"><span class="k">Severity</span><span class="v">${esc(I.severity || "—")}</span></div>
        <div class="cx-kv"><span class="k">Date</span><span class="v">${esc(I.date)}</span></div>
        <div class="cx-kv"><span class="k">Photos</span><span class="v">${C.photos.length}</span></div>
        ${I.amount ? `<div class="cx-kv"><span class="k">Your estimate</span><span class="v">${money(I.amount)}</span></div>` : ""}
        ${worst.length ? `<div class="cx-kv"><span class="k">AI damage read</span><span class="v">${esc([...new Set(worst)].join(", "))}</span></div>` : ""}
      </div>
      <div class="cx-note"><span>🔒</span><div>Filing a false claim is an offence. By submitting you confirm these details are accurate.</div></div>
      <div class="cx-actions">
        <button class="cx-btn ghost" onclick="CX.go('photos')">Back</button>
        <button class="cx-btn primary" id="submit">Submit claim</button>
      </div>
    </div>`;
  $("submit").onclick = submitClaim;
}

async function submitClaim() {
  C.step = "result"; C.result = "loading"; renderStepper();
  app().innerHTML = `<div class="cx-card"><div class="cx-center">
    <div class="cx-spin" style="width:30px;height:30px"></div>
    <div><b style="color:var(--ink);font-size:16px">Assessing your claim…</b><br>
    <span style="font-size:13px">Checking cover, reading your photos, estimating the repair.</span></div>
  </div></div>`;
  try {
    const r = await api(`/api/claims/${C.claimId}/score`, { method: "POST" });
    C.result = r;
  } catch (e) {
    C.result = { error: e.message };
  }
  render();
}

/* ============================ STEP 5 · RESULT ============================ */
function stepResult(el) {
  const r = C.result;
  if (r === "loading") return;
  if (!r || r.error) {
    el.innerHTML = `<div class="cx-card"><div class="cx-hero stop"><div class="mark">!</div>
      <h2>Something went wrong</h2><p>${esc(r?.error || "Please try again.")}</p></div>
      <div class="cx-actions"><button class="cx-btn primary" onclick="CX.go('review')">Try again</button></div></div>`;
    return;
  }
  const s = r.score || {};
  const lane = r.lane || s.lane;
  const sett = s.settlement || {};
  const aw = sett.advise_withdraw || {};

  // The advise-withdraw moment takes priority — unless the customer already chose.
  if (aw.advise_withdraw && C.withdrawChoice === null) return adviseWithdraw(el, s, aw);
  if (C.withdrawChoice === "withdraw") return withdrawn(el, aw);

  const view = laneView(lane, s, sett);
  el.innerHTML = `
    <div class="cx-card">
      <div class="cx-hero ${view.tone}">
        <div class="mark">${view.icon}</div>
        <h2>${view.title}</h2>
        <p>${view.body}</p>
      </div>
      ${view.payout ? `<div class="cx-payout">${money(view.payout)}</div><div class="cx-eta">${esc(view.eta)}</div>` : ""}
      ${view.extra || ""}
      <div class="cx-actions">
        <button class="cx-btn primary" onclick="CX.go('track')">Track my claim</button>
        <button class="cx-btn ghost" onclick="CX.restart()">File another</button>
      </div>
    </div>`;
}

function laneView(lane, s, sett) {
  const covReason = (s.coverage_state_reasons || [])[0] || "";
  const isLegalWeak = s.coverage_state === "LEGAL_WEAK" || s.legal_weak_reject_flag;
  switch (lane) {
    case "lane1_touchless":
      return { tone: "ok", icon: "✓", title: "Approved — instantly",
        body: "Your claim cleared our automated checks. No surveyor visit, no waiting.",
        payout: sett.net_payable, eta: "Paid to your registered account within minutes" };
    case "retake":
      return { tone: "wait", icon: "↺", title: "Almost there",
        body: "One or two photos weren't clear enough to assess. A quick retake and you're done.",
        extra: `<div class="cx-note warn"><span class="s">📷</span><div>${esc(retakeHint(s))}</div></div>
          <div style="margin-top:12px"><button class="cx-btn" onclick="CX.go('photos')">Add better photos</button></div>` };
    case "coverage_reject":
      if (isLegalWeak) return { tone: "look", icon: "⚖", title: "Under review",
        body: "There's a technicality on your policy, but the law is on your side here — so a specialist will review it personally rather than auto-decline. We'll be in touch." };
      return { tone: "stop", icon: "✕", title: "This isn't covered",
        body: declineReason(covReason),
        extra: `<div class="cx-note"><span class="s">↩</span><div>If you believe this is a mistake, you can request a human review — every decision is appealable.</div></div>` };
    case "lane3_investigative":
      return { tone: "look", icon: "🔍", title: "We need a closer look",
        body: "Given the details, a surveyor will assess your vehicle before we settle. This protects honest customers from fraud and keeps premiums fair.",
        eta: "A surveyor will contact you within 24 hours" };
    case "lane2_assisted":
    default:
      return { tone: "wait", icon: "⏳", title: "Your claim is in review",
        body: "We've prepared your file — a claims officer will confirm the settlement shortly. Everything's on track.",
        eta: "Expected decision within 1 working day" };
  }
}

function retakeHint(s) {
  const r = (s.lane_reasons || []).join(" ").toLowerCase();
  if (r.includes("blur")) return "One photo was blurry. In good light, hold steady and tap to focus before capturing.";
  if (r.includes("confidence")) return "We couldn't clearly read the damage. Add a straight-on close-up of the damaged area.";
  return "Add a clear front-left and a close-up of the damaged area, in daylight if you can.";
}

function declineReason(code) {
  const M = {
    policy_lapsed: "Your policy wasn't active on the date of the incident.",
    policy_not_in_force_on_incident_date: "Your policy wasn't in force on the date of the incident.",
    no_valid_licence: "The driver didn't hold a valid licence for this vehicle.",
    dui: "The policy doesn't cover incidents involving driving under the influence.",
    engine_damage_without_engine_protect: "Engine damage isn't covered without the Engine Protect add-on on your policy.",
    usage_contrary_to_limitation: "The vehicle was being used outside your policy's permitted use (e.g. commercial use on a private policy).",
  };
  if (M[code]) return M[code];
  if (String(code).startsWith("claim_type_")) return "Your policy type doesn't cover this kind of claim (for example, a third-party-only policy can't pay for damage to your own car).";
  return "Based on your policy terms, this claim isn't eligible for cover.";
}

/* ---- advise-withdraw: the delightful "don't claim this" moment ---- */
function adviseWithdraw(el, s, aw) {
  const isNcb = aw.reason === "ncb_loss_exceeds_payout";
  const payout = (s.settlement || {}).net_payable || 0;
  el.innerHTML = `
    <div class="cx-card">
      <div class="cx-hero wait"><div class="mark">💡</div>
        <h2>You can claim — but you might not want to</h2>
        <p>We ran the numbers so you don't lose out. Here's the honest picture.</p></div>
      <div class="cx-advise">
        <div class="h">💰 Claiming this could cost you money</div>
        <p>${isNcb
          ? `This claim would pay about <b>${money(payout)}</b>, but making it resets your No-Claim Bonus — costing you roughly <b>${money(aw.ncb_loss)}</b> in higher premium next year. Net, you'd be <b>${money(Math.abs(aw.net_benefit))} worse off</b> by claiming.`
          : `The assessed repair is at or below your policy deductible, so this claim would pay out <b>₹0</b> — but it would still reset your No-Claim Bonus.`}</p>
      </div>
      <div class="cx-note ok"><span class="s">✓</span><div>Skip this claim and your No-Claim Bonus stays intact for next year's renewal.</div></div>
      <div class="cx-actions">
        <button class="cx-btn primary" id="wd">Withdraw &amp; protect my bonus</button>
        <button class="cx-btn ghost" id="anyway">Claim anyway</button>
      </div>
    </div>`;
  $("wd").onclick = () => { C.withdrawChoice = "withdraw"; render(); };
  $("anyway").onclick = () => { C.withdrawChoice = "proceed"; render(); };
}

function withdrawn(el, aw) {
  const saved = Number(aw.ncb_loss) > 0
    ? `You saved roughly <b>${money(aw.ncb_loss)}</b> on next year's premium.`
    : `You've kept your claim-free record intact — that's a bigger No-Claim Bonus at renewal.`;
  el.innerHTML = `
    <div class="cx-card">
      <div class="cx-hero ok"><div class="mark">🛡️</div>
        <h2>Smart move</h2>
        <p>Your claim's been set aside and your No-Claim Bonus is protected. ${saved}</p></div>
      <div class="cx-note ok"><span class="s">✓</span><div>Changed your mind? You can still file within your policy's notification window.</div></div>
      <div class="cx-actions"><button class="cx-btn primary" onclick="CX.restart()">Done</button></div>
    </div>`;
}

/* ============================ TRACK ============================ */
async function stepTrack(el) {
  el.innerHTML = `<div class="cx-card"><div class="cx-center"><span class="cx-spin"></span> Loading your claim…</div></div>`;
  let d; try { d = await api("/api/claims/" + C.claimId); } catch (e) { el.innerHTML = `<div class="cx-card">Couldn't load. <button class="cx-btn" onclick="CX.go('track')">Retry</button></div>`; return; }
  const c = d.claim, s = d.score || {};
  const statusLabel = {
    approved: ["✓ Approved", "ok"], awaiting_officer: ["In review", "wait"],
    investigating: ["Under investigation", "look"], retake: ["Awaiting photos", "wait"],
    declined: ["Not covered", "stop"], paid: ["Settled", "ok"], scored: ["Assessed", "wait"],
  }[c.status] || ["Received", "wait"];
  const friendly = {
    claim_opened: "Claim received", photo_added: "Photo analysed",
    document_ocr: "Document read", scored: "Assessed & routed",
    duplicate_detected: "Duplicate check", decision_recorded: "Officer decision",
    settled: "Settled", policy_upsert_failed: "Policy sync",
  };
  el.innerHTML = `
    <div class="cx-card">
      <div class="cx-eyebrow">Claim ${esc(C.claimId)}</div>
      <h1 class="cx-h">${esc(statusLabel[0])}</h1>
      <p class="cx-lede">${esc(c.make || "")} ${esc(c.model || "")} · filed ${esc((c.created_at || "").slice(0, 10))}</p>
      <div style="margin-top:8px">
        ${s.line_item_estimate ? `<div class="cx-kv"><span class="k">Estimated repair</span><span class="v">${money(s.line_item_estimate)}</span></div>` : ""}
        ${s.settlement?.net_payable ? `<div class="cx-kv"><span class="k">Amount payable</span><span class="v">${money(s.settlement.net_payable)}</span></div>` : ""}
      </div>
      <div class="cx-tl" style="margin-top:16px">
        ${(d.timeline || []).map(e => `<div class="cx-tl-item"><div class="dot"></div>
          <div><div class="e">${esc(friendly[e.event] || e.event)}</div>
          <div class="t">${esc((e.created_at || "").slice(0, 16).replace("T", " "))}</div></div></div>`).join("") || "<div class='cx-center'>No updates yet.</div>"}
      </div>
      <div class="cx-actions"><button class="cx-btn ghost" onclick="CX.restart()">File another claim</button></div>
    </div>`;
}

/* ---------- nav ---------- */
function go(step) { C.step = step; render(); }
function restart() {
  C.step = "policy"; C.policy = null; C.claimId = null; C.photos = [];
  C.incident = { type: null, severity: null, date: today(), desc: "", amount: "" };
  C.result = null; C.withdrawChoice = null; render();
}
window.CX = { go, restart };
render();
