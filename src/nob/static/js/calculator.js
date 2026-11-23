/**
 * Position calculator preset helpers and singleton offline Web Audio risk chime.
 */

let sharedAudioCtx = null;

export function computeDefaultSlTp(entryPrice, side, includeTp = true) {
  const ep = Number(entryPrice);
  if (!Number.isFinite(ep) || ep <= 0) {
    return { stopLoss: '', takeProfit: '' };
  }
  if (side === 'buy') {
    return {
      stopLoss: Number((ep * 0.98).toFixed(2)),
      takeProfit: includeTp ? Number((ep * 1.04).toFixed(2)) : undefined,
    };
  }
  return {
    stopLoss: Number((ep * 1.02).toFixed(2)),
    takeProfit: includeTp ? Number((ep * 0.96).toFixed(2)) : undefined,
  };
}

export function playRiskAlertSound() {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    if (!sharedAudioCtx || sharedAudioCtx.state === 'closed') {
      sharedAudioCtx = new AudioCtx();
    }
    if (sharedAudioCtx.state === 'suspended') {
      sharedAudioCtx.resume().catch(() => {});
    }
    const osc = sharedAudioCtx.createOscillator();
    const gain = sharedAudioCtx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(587.33, sharedAudioCtx.currentTime); // D5
    osc.frequency.exponentialRampToValueAtTime(880, sharedAudioCtx.currentTime + 0.15); // A5
    gain.gain.setValueAtTime(0.3, sharedAudioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, sharedAudioCtx.currentTime + 0.35);
    osc.connect(gain);
    gain.connect(sharedAudioCtx.destination);
    osc.start();
    osc.stop(sharedAudioCtx.currentTime + 0.35);
    osc.onended = () => {
      try {
        osc.disconnect();
        gain.disconnect();
      } catch {}
    };
  } catch (e) {
    console.warn('Audio alert notice:', e);
  }
}

export function closeRiskAudioContext() {
  if (sharedAudioCtx && sharedAudioCtx.state !== 'closed') {
    try {
      sharedAudioCtx.close().catch(() => {});
    } catch {}
    sharedAudioCtx = null;
  }
}
