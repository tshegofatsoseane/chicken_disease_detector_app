//app.js 

const socket = io({ transports: ["websocket"] });

// Configuration
let PAYSTACK_PUBLIC_KEY = "";
let DOWNLOAD_COST_ZAR = 50;
let SUBSCRIPTION_COST_ZAR = 500;
let PAYSTACK_CURRENCY = "ZAR";
let IS_PREMIUM = false;

let USAGE_STATS = {
  scans_used: 0,
  scans_limit: 5,
  scans_remaining: 5,
  downloads_used: 0,
  downloads_limit: 5,
  downloads_remaining: 5,
  is_premium: false
};

let isLive = false;
let hasCapture = false;
let liveFrame = null;
let capturedFrame = null;
let latestReport = null;
let history = [];
let paymentInProgress = false;

const video = document.getElementById("video");
const placeholder = document.getElementById("placeholder");
const label = document.getElementById("label");

const DISEASES = {
  0: { name: 'Avian Influenza', severity: 'critical', description: 'Highly contagious viral infection detected.', nextSteps: ['IMMEDIATE QUARANTINE', 'Contact veterinarian urgently'], care: { immediate:'Isolate infected birds immediately.', daily:'Monitor all birds for symptoms.', weekly:'Continue surveillance.', prevention:'Vaccinate and maintain biosecurity.'} },
  1: { name: 'Splay Foot', severity: 'warning', description: 'Condition where chicks walk with legs spread outward.', nextSteps: ['Support chicks with proper bedding', 'Use leg braces if needed'], care: { immediate:'Provide soft bedding.', daily:'Check leg alignment.', weekly:'Ensure proper nutrition.', prevention:'Maintain proper brooder conditions.'} },
  2: { name: 'New Castle Disease', severity: 'critical', description: 'Highly contagious viral disease.', nextSteps: ['Isolate infected birds', 'Contact vet immediately'], care: { immediate:'Separate infected birds.', daily:'Supportive care.', weekly:'Monitor flock health.', prevention:'Maintain vaccination program.'} },
  3: { name: 'Infectious Coryza', severity: 'warning', description: 'Bacterial infection of upper respiratory tract.', nextSteps: ['Isolate sick birds', 'Administer antibiotics'], care: { immediate:'Provide clean water and feed.', daily:'Monitor for symptoms.', weekly:'Sanitize housing.', prevention:'Vaccinate and maintain hygiene.'} },
  4: { name: 'Healthy Chicken', severity: 'healthy', description: 'No disease detected. Chicken is in good health.', nextSteps: ['Continue routine care'], care: { immediate:'Maintain normal care.', daily:'Monitor flock.', weekly:'Check feed and water quality.', prevention:'Routine vaccination.'} },
  5: { name: 'Gumboro Disease', severity: 'warning', description: 'Affects immune system in young chickens.', nextSteps: ['Isolate affected birds', 'Contact vet'], care: { immediate:'Provide clean environment.', daily:'Hydration and nutrition.', weekly:'Check vaccination schedule.', prevention:'Stress reduction and hygiene.'} },
  6: { name: 'Healthy', severity: 'healthy', description: 'No visible signs of illness.', nextSteps: ['Maintain regular care'], care: { immediate:'Feed and water as usual.', daily:'Monitor flock.', weekly:'Routine health checks.', prevention:'Maintain biosecurity.'} },
  7: { name: 'Dead Chickens', severity: 'critical', description: 'Mortality detected in flock.', nextSteps: ['Identify cause', 'Remove carcasses', 'Sanitize area'], care: { immediate:'Remove dead birds.', daily:'Monitor flock closely.', weekly:'Investigate causes.', prevention:'Vaccination and biosecurity.'} },
  8: { name: 'Chicken Feeding', severity: 'info', description: 'Monitoring feeding activity.', nextSteps: ['Ensure feed quality', 'Maintain schedule'], care: { immediate:'Check feed supply.', daily:'Observe eating behavior.', weekly:'Adjust feed if necessary.', prevention:'Store feed properly.'} }
};

// Mobile menu toggle
function toggleMobileMenu() {
  const sidebar = document.getElementById('mobile-sidebar');
  const overlay = document.getElementById('mobile-overlay');
  sidebar.classList.toggle('open');
  overlay.classList.toggle('show');
}

// Update usage badge for both desktop and mobile
function updateUsageBadge() {
  if (IS_PREMIUM || USAGE_STATS.is_premium) {
    document.getElementById("usage-badge").classList.add("hidden");
    document.getElementById("usage-badge-mobile").classList.add("hidden");
    return;
  }
  
  const scansText = USAGE_STATS.scans_remaining !== null 
    ? `${USAGE_STATS.scans_remaining} scans left`
    : 'Unlimited scans';
  const downloadsText = USAGE_STATS.downloads_remaining !== null
    ? `${USAGE_STATS.downloads_remaining} downloads left`
    : 'Unlimited downloads';
  
  // Desktop
  document.getElementById("scans-remaining").textContent = scansText;
  document.getElementById("downloads-remaining").textContent = downloadsText;
  document.getElementById("usage-badge").classList.remove("hidden");
  
  // Mobile
  document.getElementById("scans-remaining-mobile").textContent = scansText;
  document.getElementById("downloads-remaining-mobile").textContent = downloadsText;
  document.getElementById("usage-badge-mobile").classList.remove("hidden");
  
  if (USAGE_STATS.scans_remaining !== null && USAGE_STATS.scans_remaining <= 1) {
    document.getElementById("usage-badge").classList.add("bg-yellow-50", "text-yellow-700");
    document.getElementById("usage-badge-mobile").classList.add("bg-yellow-50", "text-yellow-700");
  }
}

async function loadSubscriptionStatus() {
  try {
    const res = await fetch("/api/subscription-status");
    const status = await res.json();
    IS_PREMIUM = status.is_premium;
    
    if (IS_PREMIUM) {
      document.getElementById("premium-badge").classList.remove("hidden");
      document.getElementById("subscribe-btn").textContent = "✅ Premium";
      document.getElementById("subscribe-btn").disabled = true;
      document.getElementById("subscribe-btn-mobile").textContent = "✅ Premium Active";
      document.getElementById("subscribe-btn-mobile").disabled = true;
      document.getElementById("usage-badge").classList.add("hidden");
      document.getElementById("usage-badge-mobile").classList.add("hidden");
    } else {
      updateUsageBadge();
    }
  } catch (e) {
    console.error("Error loading subscription status:", e);
  }
}

async function loadUsageStats() {
  try {
    const res = await fetch("/api/usage-stats");
    USAGE_STATS = await res.json();
    updateUsageBadge();
  } catch (e) {
    console.error("Error loading usage stats:", e);
  }
}

function openSubscriptionModal() {
  if (IS_PREMIUM) {
    alert("You already have an active premium subscription!");
    return;
  }
  document.getElementById("subscription-modal").classList.remove("hidden");
}

function closeSubscriptionModal() {
  document.getElementById("subscription-modal").classList.add("hidden");
}

async function processSubscription() {
  if (paymentInProgress) return;
  paymentInProgress = true;
  
  const payBtn = document.getElementById("subscribe-pay-btn");
  payBtn.disabled = true;
  payBtn.textContent = "Processing...";

  try {
    const initRes = await fetch("/api/initialize-subscription", {
      method: "POST",
      headers: { "Content-Type": "application/json" }
    });

    const initData = await initRes.json();
    
    if (initData.status !== "ok" || !initData.payment_url) {
      alert("❌ Failed to initialize subscription: " + (initData.message || "Unknown error"));
      paymentInProgress = false;
      payBtn.disabled = false;
      payBtn.textContent = "🔒 Subscribe Now";
      return;
    }

    // Redirect to Paystack. Backend will verify on callback via URL params
    window.location.href = initData.payment_url;
    
  } catch (e) {
    console.error("Subscription error:", e);
    alert("❌ Subscription error: " + e.message);
    paymentInProgress = false;
    payBtn.disabled = false;
    payBtn.textContent = "🔒 Subscribe Now";
  }
}

async function verifySubscription(reference) {
  try {
    const verifyRes = await fetch("/api/verify-subscription", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reference })
    });

    const verifyData = await verifyRes.json();
    
    if (verifyData.status === "ok") {
      alert("✅ Subscription activated! Welcome to BioDrone Premium! 💎");
      closeSubscriptionModal();
      await loadSubscriptionStatus();
      location.reload();
    } else {
      alert("❌ Subscription verification failed: " + (verifyData.message || "Unknown error"));
    }
  } catch (e) {
    console.error("Verification error:", e);
    alert("Verification error: " + e.message);
  } finally {
    paymentInProgress = false;
  }
}

async function loadHistory() {
  try {
    const res = await fetch("/get-history");
    history = await res.json();
    renderHistory();
  } catch (e) {
    console.error("Error loading history:", e);
    const lists = [document.getElementById("history-list"), document.getElementById("history-list-mobile")];
    lists.forEach(list => {
      if (list) list.innerHTML = '<li class="text-gray-400 italic">Error loading history</li>';
    });
  }
}

socket.on("new_frame", d => {
  if (isLive) {
    liveFrame = "data:image/jpeg;base64," + d.frame;
    video.src = liveFrame;
    video.classList.remove("hidden");
    placeholder.classList.add("hidden");
    label.innerText = "🔴 Live Monitoring...";
  }
});

socket.on("pi_stopped", () => {
  isLive = false;
  liveFrame = null;
  if (!capturedFrame) {
    video.classList.add("hidden");
    placeholder.classList.remove("hidden");
  }
  label.innerText = "🛑 Feed stopped.";
  toggleButtons(true);
});

socket.on("frame_captured", d => {
  hasCapture = true;
  isLive = false;
  capturedFrame = "data:image/jpeg;base64," + d.frame;
  latestReport = { image: capturedFrame };
  video.src = capturedFrame;
  video.classList.remove("hidden");
  placeholder.classList.add("hidden");
  label.innerText = "📸 Frame captured!";
  document.getElementById("analyze").disabled = false;
  document.getElementById("analyze-sidebar")?.setAttribute("disabled", "false");
  document.getElementById("analyze-mobile")?.setAttribute("disabled", "false");
  document.getElementById("capture-sidebar")?.setAttribute("disabled", "false");
  document.getElementById("capture-mobile")?.setAttribute("disabled", "false");
  document.getElementById("stop").disabled = true;
  document.getElementById("start").disabled = false;
  document.getElementById("reset").disabled = false;
});

socket.on("frame_analyzed", d => {
  closeLoading();
  
  // Check for limit reached error
  if (d.limit_reached) {
    alert("❌ " + d.message);
    if (confirm("Upgrade to Premium for unlimited scans?")) {
      openSubscriptionModal();
    }
    return;
  }
  
  const disease = DISEASES[d.disease_id] || DISEASES[4];
  latestReport = { disease, confidence: d.confidence, image: capturedFrame };
  showReport(disease, d.confidence);
  loadUsageStats();
});

function triggerPi() {
  fetch("/trigger-pi", { method:"POST" });
  isLive = true;
  hasCapture = false;
  liveFrame = null;
  capturedFrame = null;
  latestReport = null;
  video.classList.add("hidden");
  placeholder.classList.remove("hidden");
  label.innerText = "🔄 Starting feed...";
  toggleButtons(false);
  document.getElementById("reset").disabled = false;
}

function stopPi() {
  fetch("/reset-pi", { method:"POST" });
  isLive = false;
  liveFrame = null;
  if (!capturedFrame) {
    video.classList.add("hidden");
    placeholder.classList.remove("hidden");
  }
  label.innerText = "🛑 Feed stopped.";
  toggleButtons(true);
}

function captureFrame() {
  fetch("/capture-frame", { method:"POST" }).then(() => {
    fetch("/reset-pi", { method:"POST" });
    isLive = false;
    hasCapture = true;
    video.src = capturedFrame;
    video.classList.remove("hidden");
    placeholder.classList.add("hidden");
    label.innerText = "📸 Frame captured!";
    document.getElementById("analyze").disabled = false;
    document.getElementById("analyze-sidebar")?.setAttribute("disabled", "false");
    document.getElementById("analyze-mobile")?.setAttribute("disabled", "false");
    document.getElementById("stop").disabled = true;
    document.getElementById("start").disabled = false;
    document.getElementById("reset").disabled = false;
  }).catch(e => {
    console.error("Capture error:", e);
    alert("Error capturing frame");
  });
}

function analyzeFrame() {
  if(!hasCapture) return alert("Capture a frame first!");
  
  if (!IS_PREMIUM && USAGE_STATS.scans_remaining !== null && USAGE_STATS.scans_remaining <= 0) {
    if (confirm("You've reached your free scan limit (5/day). Upgrade to Premium for unlimited scans?")) {
      openSubscriptionModal();
    }
    return;
  }
  
  showLoading();
  fetch("/analyze-frame", { method:"POST" })
    .catch(e => {
      console.error("Analysis error:", e);
      closeLoading();
      alert("Error analyzing frame");
    });
}

function resetLiveDetection() {
  liveFrame = null;
  capturedFrame = null;
  latestReport = null;
  isLive = false;
  hasCapture = false;

  video.src = "";
  video.classList.add("hidden");
  placeholder.classList.remove("hidden");
  label.innerText = "Camera feed not active.";

  document.getElementById("start").disabled = false;
  document.getElementById("stop").disabled = true;
  document.getElementById("capture").disabled = true;
  document.getElementById("capture-sidebar")?.setAttribute("disabled", "true");
  document.getElementById("capture-mobile")?.setAttribute("disabled", "true");
  document.getElementById("analyze").disabled = true;
  document.getElementById("analyze-sidebar")?.setAttribute("disabled", "true");
  document.getElementById("analyze-mobile")?.setAttribute("disabled", "true");
  document.getElementById("reset").disabled = true;
}

function toggleButtons(enable) {
  document.getElementById("start").disabled = !enable;
  document.getElementById("stop").disabled = enable;
  document.getElementById("capture").disabled = enable;
  document.getElementById("capture-sidebar")?.setAttribute("disabled", enable ? "true" : "false");
  document.getElementById("capture-mobile")?.setAttribute("disabled", enable ? "true" : "false");
}

function openModal(){ document.getElementById("analysis-modal").classList.remove("hidden"); }
function closeModal(){ document.getElementById("analysis-modal").classList.add("hidden"); }
function showLoading(){ document.getElementById("loading-overlay").classList.remove("hidden"); }
function closeLoading(){ document.getElementById("loading-overlay").classList.add("hidden"); }
function openPaymentModal(){ document.getElementById("payment-modal").classList.remove("hidden"); }
function closePaymentModal(){ document.getElementById("payment-modal").classList.add("hidden"); }

function showReport(disease, confidence) {
  const modalContent = document.getElementById("modal-content");
  const modalImage = document.getElementById("modal-image");

  modalImage.src = latestReport?.image || "";

  modalContent.innerHTML = `
    <div>
      <h3 class="text-lg sm:text-xl font-semibold text-purple-700">${disease.name}</h3>
      <p class="text-gray-600 mt-2 text-sm sm:text-base">${disease.description}</p>
      <div class="mt-3 sm:mt-4">
        <h4 class="font-semibold text-purple-700 mb-2 text-sm sm:text-base">Next Steps</h4>
        <ul class="list-disc list-inside text-gray-700 space-y-1 text-sm sm:text-base">
          ${disease.nextSteps.map(s=>`<li>${s}</li>`).join('')}
        </ul>
      </div>
      <div class="mt-3 sm:mt-4">
        <h4 class="font-semibold text-purple-700 mb-2 text-sm sm:text-base">Care Plan</h4>
        <ul class="list-disc list-inside text-gray-700 space-y-1 text-sm">
          <li><strong>Immediate:</strong> ${disease.care.immediate}</li>
          <li><strong>Daily:</strong> ${disease.care.daily}</li>
          <li><strong>Weekly:</strong> ${disease.care.weekly}</li>
          <li><strong>Prevention:</strong> ${disease.care.prevention}</li>
        </ul>
      </div>
      <p class="mt-3 sm:mt-4 text-xs sm:text-sm font-semibold text-gray-600">Confidence: ${(confidence*100).toFixed(1)}%</p>
    </div>`;

  const badge = document.getElementById("severity-badge");
  badge.textContent = disease.severity.toUpperCase();
  if (disease.severity === 'critical') {
    badge.className = "px-3 py-1 rounded-full bg-red-600 text-white text-xs sm:text-sm font-medium";
  } else if (disease.severity === 'warning') {
    badge.className = "px-3 py-1 rounded-full bg-yellow-500 text-white text-xs sm:text-sm font-medium";
  } else if (disease.severity === 'healthy') {
    badge.className = "px-3 py-1 rounded-full bg-green-500 text-white text-xs sm:text-sm font-medium";
  } else {
    badge.className = "px-3 py-1 rounded-full bg-blue-500 text-white text-xs sm:text-sm font-medium";
  }

  openModal();
}

async function initiateDownload() {
  if (!latestReport) return alert("No report to download!");
  
  if (IS_PREMIUM) {
    alert("✅ Premium user - downloading your report for free!");
    downloadPDF();
    return;
  }
  
  const disease = latestReport.disease;
  document.getElementById("payment-disease").textContent = disease.name;
  document.getElementById("payment-amount").textContent = `R${DOWNLOAD_COST_ZAR}.00`;
  document.getElementById("payment-total").textContent = `R${DOWNLOAD_COST_ZAR}.00`;
  
  closeModal();
  openPaymentModal();
}

async function processPayment() {
  if (paymentInProgress) return;
  paymentInProgress = true;
  
  const payBtn = document.getElementById("pay-btn");
  payBtn.disabled = true;
  payBtn.textContent = "Processing...";

  try {
    const disease = latestReport.disease;
    
    console.log("💳 Initializing payment for:", disease.name);
    
    const initRes = await fetch("/api/initialize-payment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        amount: DOWNLOAD_COST_ZAR,
        disease_name: disease.name
      })
    });

    const initData = await initRes.json();
    console.log("📊 Payment init response:", initData);
    
    // Handle premium user - free download
    if (initData.is_premium) {
      alert("✅ Premium user - downloading your report for free!");
      closePaymentModal();
      downloadPDF();
      await loadUsageStats();
      paymentInProgress = false;
      payBtn.disabled = false;
      payBtn.textContent = "🔒 Pay with Paystack";
      return;
    }
    
    // Handle error or missing payment_url
    if (!initData.status || initData.status !== "ok" || !initData.payment_url) {
      const errorMsg = initData.message || "Failed to initialize payment";
      console.error("❌ Payment initialization failed:", errorMsg);
      alert("❌ " + errorMsg);
      paymentInProgress = false;
      payBtn.disabled = false;
      payBtn.textContent = "🔒 Pay with Paystack";
      return;
    }

    console.log("🚀 Redirecting to Paystack:", initData.payment_url);
    // Redirect to Paystack payment page
    window.location.href = initData.payment_url;
    
  } catch (e) {
    console.error("Payment error:", e);
    alert("❌ Payment error: " + e.message);
    paymentInProgress = false;
    payBtn.disabled = false;
    payBtn.textContent = "🔒 Pay with Paystack";
  }
}

async function verifyPayment(reference) {
  try {
    const verifyRes = await fetch("/api/verify-payment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reference })
    });

    const verifyData = await verifyRes.json();
    
    if (verifyData.status === "ok" && verifyData.can_download) {
      alert("✅ Payment successful! Downloading your report...");
      downloadPDF();
    } else {
      alert("❌ Payment verification failed: " + (verifyData.message || "Unknown error"));
    }
  } catch (e) {
    console.error("Verification error:", e);
    alert("Verification error: " + e.message);
  } finally {
    paymentInProgress = false;
  }
}

async function saveToHistory() {
  if(!latestReport) return;
  const { disease, confidence, image } = latestReport;
  const record = { disease, confidence, image };
  try {
    await fetch("/save-result",{ method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(record) });
    loadHistory();
    alert("✅ Report saved to history!");
    closeModal();
  } catch (e) {
    console.error("Error saving:", e);
    alert("Error saving to history");
  }
}

function renderHistory() {
  const historyHTML = history.length === 0 
    ? '<li class="text-gray-400 italic">No detections yet.</li>'
    : history.map((h, i) => {
        const disease = h.disease || DISEASES[4];
        return `
        <li class="flex items-center space-x-3 p-2 rounded-lg border border-gray-200 hover:bg-gray-50 cursor-pointer transition" onclick='viewHistory(${i})'>
          <img src="${h.image}" class="w-12 h-12 rounded-lg object-cover border flex-shrink-0" alt="">
          <div class="flex-1 min-w-0">
            <p class="font-medium text-gray-800 truncate text-sm">${disease.name}</p>
            <p class="text-xs text-gray-500">${new Date(h.timestamp || new Date()).toLocaleString()}</p>
          </div>
          <span class="${disease.severity === 'critical' ? 'text-red-500' : disease.severity === 'warning' ? 'text-yellow-500' : 'text-green-500'} font-semibold text-sm">${(h.confidence*100).toFixed(0)}%</span>
        </li>`;
      }).join('');
  
  const historyList = document.getElementById("history-list");
  const historyListMobile = document.getElementById("history-list-mobile");
  
  if (historyList) historyList.innerHTML = historyHTML;
  if (historyListMobile) historyListMobile.innerHTML = historyHTML;
}

function viewHistory(index) {
  const record = history[index];
  if(!record) return;
  latestReport = record;
  capturedFrame = record.image;
  const disease = record.disease || DISEASES[4];
  showReport(disease, record.confidence);
  
  if (window.innerWidth < 1024) {
    toggleMobileMenu();
  }
}

async function downloadPDF() {
  if(!latestReport) return;
  const { jsPDF } = window.jspdf;
  const pdf = new jsPDF();
  const { disease, confidence, image } = latestReport;

  try {
    pdf.setFontSize(24);
    pdf.setTextColor(124, 58, 237);
    pdf.text("Kgosi BioDrone", 20, 20);
    pdf.setFontSize(10);
    pdf.setTextColor(107, 114, 128);
    pdf.text("AI-Powered Poultry Health Monitoring", 20, 28);

    if(image) {
      try {
        pdf.addImage(image, 'JPEG', 20, 40, 80, 60);
      } catch (e) {
        console.log("Could not add image to PDF");
      }
    }

    let yPos = image ? 110 : 50;
    
    pdf.setFontSize(14);
    pdf.setTextColor(0, 0, 0);
    pdf.text("Analysis Report", 20, yPos);
    yPos += 10;

    pdf.setFontSize(11);
    pdf.setTextColor(31, 41, 55);
    pdf.text(`Disease: ${disease.name}`, 20, yPos);
    yPos += 8;
    pdf.text(`Severity: ${disease.severity.toUpperCase()}`, 20, yPos);
    yPos += 8;
    pdf.text(`Confidence: ${(confidence*100).toFixed(1)}%`, 20, yPos);
    yPos += 8;
    pdf.text(`Date: ${new Date().toLocaleString()}`, 20, yPos);
    yPos += 15;

    pdf.setFontSize(10);
    pdf.setTextColor(107, 114, 128);
    pdf.text("Description:", 20, yPos);
    yPos += 5;
    const descLines = pdf.splitTextToSize(disease.description, 170);
    pdf.text(descLines, 20, yPos);
    yPos += descLines.length * 5 + 5;

    pdf.text("Recommended Next Steps:", 20, yPos);
    yPos += 5;
    disease.nextSteps.forEach(step => {
      const lines = pdf.splitTextToSize(`• ${step}`, 170);
      pdf.text(lines, 20, yPos);
      yPos += lines.length * 4 + 2;
    });

    yPos += 5;

    pdf.text("Care Instructions:", 20, yPos);
    yPos += 5;
    for (const [key, value] of Object.entries(disease.care)) {
      const label = key.charAt(0).toUpperCase() + key.slice(1);
      const lines = pdf.splitTextToSize(`${label}: ${value}`, 170);
      pdf.text(lines, 20, yPos);
      yPos += lines.length * 4 + 2;
    }

    pdf.setFontSize(8);
    pdf.setTextColor(156, 163, 175);
    pdf.text("Generated by Kgosi BioDrone AI", 20, pdf.internal.pageSize.height - 10);

    pdf.save(`${disease.name.replace(/\s+/g,"_")}_Report_${new Date().getTime()}.pdf`);
  } catch (e) {
    console.error("PDF generation error:", e);
    alert("Error generating PDF: " + e.message);
  }
}

function checkConnection() {
  fetch("/health")
    .then(r => r.json())
    .then(d => {
      alert(`✅ Server: ${d.status}\n📡 Pi Feed: ${d.pi_feed ? 'Active' : 'Inactive'}\n📸 Frame: ${d.has_frame ? 'Yes' : 'No'}`);
    })
    .catch(e => alert("❌ Connection failed: " + e.message));
}

async function initializePaymentConfig() {
  try {
    const res = await fetch("/api/payment-config");
    const config = await res.json();
    DOWNLOAD_COST_ZAR = config.download_cost_zar;
    SUBSCRIPTION_COST_ZAR = config.subscription_cost_zar;
    PAYSTACK_CURRENCY = config.currency;
    PAYSTACK_PUBLIC_KEY = config.paystack_public_key;
  } catch (e) {
    console.error("Error loading payment config:", e);
  }
}

async function checkPaymentCallback() {
  const urlParams = new URLSearchParams(window.location.search);
  const reference = urlParams.get('reference');
  const trxref = urlParams.get('trxref');
  
  const paymentRef = reference || trxref;
  
  if (paymentRef) {
    showLoading();
    
    try {
      // Try to verify as payment first
      await verifyPayment(paymentRef);
    } catch (error) {
      console.error('Payment verification error:', error);
      // If it fails, might be a subscription
      try {
        await verifySubscription(paymentRef);
      } catch (subError) {
        console.error('Subscription verification error:', subError);
        alert('Error verifying payment. Reference: ' + paymentRef);
      }
    } finally {
      closeLoading();
    }
    
    // Clean up URL
    window.history.replaceState({}, document.title, window.location.pathname);
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  await initializePaymentConfig();
  await loadSubscriptionStatus();
  await loadUsageStats();
  await checkPaymentCallback();
  loadHistory();
  console.log("🚀 BioDrone mobile interface loaded");
});