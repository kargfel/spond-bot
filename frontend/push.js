/**
 * SpondBot Frontend — Web Push on this device (member dashboard).
 *
 * Talks to the browser (permission, PushManager) and to /api/v1/push. What the state
 * means and which text to show is decided by Core.pushState (unit tested).
 * Exposed as window.SpondPush; app.js calls disable() on sign-out.
 */
(function () {
  const supported = () =>
    "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  /** The worker is ready once it has activated; fail instead of hanging if it never registered. */
  function readyRegistration() {
    const timeout = new Promise((_, reject) =>
      setTimeout(() => reject(new ApiError("The app is still starting. Try again in a moment.", 0)), 5000));
    return Promise.race([navigator.serviceWorker.ready, timeout]);
  }

  /** This device's push subscription, or null. Never waits for a worker that is not there. */
  async function currentSubscription() {
    if (!supported()) return null;
    try {
      const reg = await navigator.serviceWorker.getRegistration();
      return reg ? await reg.pushManager.getSubscription() : null;
    } catch {
      return null;
    }
  }

  async function config() {
    try {
      return await apiJson("/push/config");
    } catch {
      return { enabled: false, public_key: null };
    }
  }

  const sameKey = (sub, key) => {
    const have = sub.options && sub.options.applicationServerKey;
    if (!have) return true;
    const a = new Uint8Array(have);
    return a.length === key.length && a.every((b, i) => b === key[i]);
  };

  /** Core.pushState for this device, from live browser and server facts. */
  async function state() {
    const cfg = await config();
    const sub = cfg.enabled ? await currentSubscription() : null;
    return Core.pushState({
      serverEnabled: cfg.enabled,
      supported: supported(),
      ios: Pwa.ios(),
      standalone: Pwa.standalone(),
      permission: supported() ? Notification.permission : "default",
      subscribed: Boolean(sub),
    });
  }

  /** Must be called from a click: the permission prompt is only allowed right after a gesture. */
  async function enable() {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      throw new ApiError(
        permission === "denied"
          ? "Notifications are blocked for SpondBot. Allow them in your browser settings."
          : "Notifications were not turned on.",
        0,
      );
    }
    const cfg = await apiJson("/push/config");
    if (!cfg.enabled) throw new ApiError("Notifications are not set up on this server.", 503);
    const key = Core.urlBase64ToBytes(cfg.public_key);
    const reg = await readyRegistration();

    let sub = await reg.pushManager.getSubscription();
    // A server key that changed since the device subscribed would make every push bounce.
    if (sub && !sameKey(sub, key)) {
      await sub.unsubscribe();
      sub = null;
    }
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    try {
      await apiJson("/push/subscribe", "POST", sub.toJSON());
    } catch (err) {
      await sub.unsubscribe().catch(() => {}); // stay consistent: no device the server does not know
      throw err;
    }
  }

  /** Forgets this device on the server and in the browser. Safe to call when nothing is subscribed. */
  async function disable() {
    const sub = await currentSubscription();
    if (!sub) return;
    await api("/push/unsubscribe", "POST", { endpoint: sub.endpoint }).catch(() => {});
    await sub.unsubscribe().catch(() => {});
  }

  /** Sends a test notification to the member's devices. Resolves to { devices, delivered }. */
  const test = () => apiJson("/push/test", "POST");

  /** The member's notification settings (shared by all their devices). */
  const preferences = () => apiJson("/push/preferences");

  /** Saves all settings at once; resolves to what the server stored. */
  const savePreferences = (prefs) => apiJson("/push/preferences", "PUT", prefs);

  window.SpondPush = { state, enable, disable, test, supported, preferences, savePreferences };
})();
