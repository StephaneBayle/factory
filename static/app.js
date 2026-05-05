const startButton = document.getElementById("startButton");
const stopButton = document.getElementById("stopButton");
const clearButton = document.getElementById("clearButton");
const fullscreenButton = document.getElementById("fullscreenButton");
const statusBadge = document.getElementById("statusBadge");
const modeLabel = document.getElementById("modeLabel");
const directionLabel = document.getElementById("directionLabel");
const sessionLabel = document.getElementById("sessionLabel");
const roleLabel = document.getElementById("roleLabel");
const sampleRateLabel = document.getElementById("sampleRate");
const transportStatsLabel = document.getElementById("transportStats");
const micLevelLabel = document.getElementById("micLevel");
const permissionStateLabel = document.getElementById("permissionState");
const deviceStateLabel = document.getElementById("deviceState");
const audienceStateLabel = document.getElementById("audienceState");
const translationOutput = document.getElementById("translationOutput");
const sourceOutput = document.getElementById("sourceOutput");
const toolbar = document.querySelector(".toolbar");
const sourceCard = document.querySelector(".source-card");

let socket;
let isRunning = false;
let audioContext;
let mediaStream;
let sourceNode;
let workletNode;
let muteNode;
const TARGET_SAMPLE_RATE = 16000;

function deriveContext() {
  const params = new URLSearchParams(location.search);
  const segments = location.pathname.split("/").filter(Boolean);
  let role = params.get("role") || "control";
  let sessionId = params.get("session") || "default";

  if (segments[0] === "control" && segments[1]) {
    role = "control";
    sessionId = segments[1];
  } else if (segments[0] === "display" && segments[1]) {
    role = "display";
    sessionId = segments[1];
  }

  return {
    role: role === "display" ? "display" : "control",
    sessionId,
  };
}

const appContext = deriveContext();

function updateShellForRole() {
  sessionLabel.textContent = `Session: ${appContext.sessionId}`;
  roleLabel.textContent =
    appContext.role === "display" ? "Écran public" : "Console opérateur";

  if (appContext.role === "display") {
    toolbar.style.display = "none";
    sourceCard.style.display = "none";
    micLevelLabel.style.display = "none";
    permissionStateLabel.style.display = "none";
    deviceStateLabel.style.display = "none";
    transportStatsLabel.style.display = "none";
  }
}

function showLocalError(message) {
  statusBadge.textContent = message;
  document.body.classList.remove("listening");
  isRunning = false;
  console.error(message);
}

function describeMediaError(error) {
  if (!error) {
    return "Erreur micro inconnue.";
  }
  const name = error.name || "Error";
  const detail = error.message ? `: ${error.message}` : "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError") {
    if (location.hostname === "localhost") {
      return `${name}${detail} Le micro reste refusé sur localhost. Cela indique souvent une limite du navigateur embarqué plutôt qu'un problème de votre app. Testez cette page dans Chrome pour confirmer.`;
    }
    return `${name}${detail} Vérifiez la permission du site pour cette origine. Essayez aussi http://localhost:8000 au lieu de http://127.0.0.1:8000.`;
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return `${name}${detail} Aucun micro détecté par le navigateur.`;
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return `${name}${detail} Le micro existe mais est occupé ou inaccessible.`;
  }
  if (name === "OverconstrainedError") {
    return `${name}${detail} Contrainte audio non compatible avec le périphérique.`;
  }
  return `${name}${detail}`;
}

async function refreshDiagnostics() {
  if (appContext.role !== "control") {
    return;
  }

  try {
    if (navigator.permissions && navigator.permissions.query) {
      const result = await navigator.permissions.query({ name: "microphone" });
      permissionStateLabel.textContent = `Permission micro: ${result.state}`;
      result.onchange = () => {
        permissionStateLabel.textContent = `Permission micro: ${result.state}`;
      };
    } else {
      permissionStateLabel.textContent = "Permission micro: API indisponible";
    }
  } catch {
    permissionStateLabel.textContent = "Permission micro: inconnue";
  }

  try {
    if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const audioInputs = devices.filter((device) => device.kind === "audioinput");
      deviceStateLabel.textContent = `Entrées audio: ${audioInputs.length}`;
    } else {
      deviceStateLabel.textContent = "Entrées audio: API indisponible";
    }
  } catch {
    deviceStateLabel.textContent = "Entrées audio: inconnues";
  }
}

function connectSocket() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(
    `${protocol}://${location.host}/ws/${encodeURIComponent(appContext.sessionId)}?role=${appContext.role}`
  );

  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "state") {
      renderState(payload);
    }
    if (payload.type === "error") {
      statusBadge.textContent = payload.message;
      document.body.classList.remove("listening");
      isRunning = false;
    }
  });

  socket.addEventListener("close", () => {
    statusBadge.textContent = "Connexion fermée";
  });
}

function renderState(payload) {
  isRunning = payload.running;
  statusBadge.textContent = payload.status;
  modeLabel.textContent = payload.modeLabel;
  directionLabel.textContent = payload.directionLabel || "Direction: en attente";
  sampleRateLabel.textContent = payload.sampleRate
    ? `${payload.sampleRate} Hz`
    : "";
  transportStatsLabel.textContent = payload.audioChunksReceived
    ? `${payload.audioChunksReceived} paquets audio`
    : "0 paquet audio";
  audienceStateLabel.textContent = `Écrans connectés: ${payload.viewerClientCount || 0}`;

  const translationLines = payload.translationLines || [];
  translationOutput.textContent =
    translationLines.join("\n") || "En attente de transcription...";

  const sourceLines = payload.sourceLines || [];
  const liveSource = payload.liveSource || "";
  const sourceParts = [...sourceLines];
  if (liveSource) {
    sourceParts.push(liveSource);
  }
  sourceOutput.textContent =
    sourceParts.join("\n") || "Aucun flux audio reçu pour le moment.";

  if (payload.running) {
    document.body.classList.add("listening");
  } else {
    document.body.classList.remove("listening");
  }
}

function sendAction(action, extra = {}) {
  if (appContext.role !== "control") {
    return;
  }
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  socket.send(JSON.stringify({ action, ...extra }));
}

async function ensureAudioPipeline() {
  if (audioContext) {
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error("Le navigateur ne supporte pas l'accès micro (getUserMedia indisponible).");
  }
  if (!window.AudioWorkletNode) {
    throw new Error("Le navigateur ne supporte pas AudioWorklet.");
  }

  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    },
  });

  audioContext = new AudioContext();
  await audioContext.audioWorklet.addModule("/static/audio-processor.js");

  sourceNode = audioContext.createMediaStreamSource(mediaStream);
  workletNode = new AudioWorkletNode(audioContext, "pcm-forwarder");
  muteNode = audioContext.createGain();
  muteNode.gain.value = 0;

  workletNode.port.onmessage = (event) => {
    if (event.data?.type === "meter") {
      micLevelLabel.textContent = `Micro: ${Math.round((event.data.peak || 0) * 100)}%`;
      return;
    }
    if (socket && socket.readyState === WebSocket.OPEN && isRunning) {
      socket.send(event.data);
    }
  };
  workletNode.port.postMessage({
    type: "config",
    targetSampleRate: TARGET_SAMPLE_RATE,
  });

  sourceNode.connect(workletNode);
  workletNode.connect(muteNode);
  muteNode.connect(audioContext.destination);
}

async function stopAudioPipeline() {
  if (workletNode) {
    workletNode.disconnect();
    workletNode.port.onmessage = null;
    workletNode = null;
  }
  if (sourceNode) {
    sourceNode.disconnect();
    sourceNode = null;
  }
  if (muteNode) {
    muteNode.disconnect();
    muteNode = null;
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }
  if (audioContext) {
    await audioContext.close();
    audioContext = null;
  }
}

startButton.addEventListener("click", async () => {
  try {
    if (appContext.role !== "control") {
      return;
    }
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      throw new Error("La connexion temps réel avec le serveur n'est pas encore ouverte.");
    }
    statusBadge.textContent = "Initialisation micro...";
    await ensureAudioPipeline();
    if (audioContext.state === "suspended") {
      await audioContext.resume();
    }
    statusBadge.textContent = "Démarrage...";
    sendAction("start", {
      sampleRate: TARGET_SAMPLE_RATE,
    });
  } catch (error) {
    showLocalError(describeMediaError(error));
    await refreshDiagnostics();
  }
});

stopButton.addEventListener("click", async () => {
  sendAction("stop");
  await stopAudioPipeline();
});

clearButton.addEventListener("click", () => {
  sendAction("clear");
  transportStatsLabel.textContent = "0 paquet audio";
});

fullscreenButton.addEventListener("click", async () => {
  if (!document.fullscreenElement) {
    await document.documentElement.requestFullscreen();
  } else {
    await document.exitFullscreen();
  }
});

window.addEventListener("beforeunload", () => {
  sendAction("stop");
});

updateShellForRole();
connectSocket();
refreshDiagnostics();
