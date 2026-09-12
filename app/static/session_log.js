// app/static/session_log.js

(function () {
    const startedAt = Date.now();
  
    function getEventId() {
      if (document.body && document.body.dataset.eventId) {
        return document.body.dataset.eventId;
      }
  
      const match = window.location.pathname.match(/\/events\/(\d+)/);
      return match ? match[1] : null;
    }
  
    function shortText(text) {
      if (!text) return null;
      return text.replace(/\s+/g, " ").trim().slice(0, 120);
    }
  
    function elementInfo(el) {
      if (!el) return {};
  
      return {
        tag: el.tagName ? el.tagName.toLowerCase() : null,
        id: el.id || null,
        name: el.getAttribute("name") || null,
        type: el.getAttribute("type") || null,
        text: shortText(el.innerText || el.value || el.getAttribute("aria-label")),
        href: el.href ? new URL(el.href).pathname : null,
        data_log_action: el.dataset.logAction || null,
        target_type: el.dataset.targetType || null,
        target_id: el.dataset.targetId || null,
      };
    }
  
    function send(action, details) {
      const payload = {
        event_id: getEventId(),
        action: action,
        page: window.location.pathname,
        phase: document.body ? document.body.dataset.phase || null : null,
        target_type: details && details.target_type ? details.target_type : null,
        target_id: details && details.target_id ? details.target_id : null,
        details: Object.assign(
          {
            title: document.title,
            url_path: window.location.pathname,
            url_query_keys: Array.from(new URLSearchParams(window.location.search).keys()),
            viewport: {
              width: window.innerWidth,
              height: window.innerHeight,
            },
          },
          details || {}
        ),
      };
  
      const body = JSON.stringify(payload);
  
      try {
        const blob = new Blob([body], { type: "application/json" });
  
        if (navigator.sendBeacon) {
          const ok = navigator.sendBeacon("/api/session-log/client", blob);
          if (ok) return;
        }
      } catch (e) {}
  
      fetch("/api/session-log/client", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: body,
        keepalive: true,
      }).catch(function () {});
    }
  
    window.sessionLog = {
      send: send,
    };
  
    window.addEventListener("load", function () {
      send("PAGE_VIEW", {
        referrer_path: document.referrer ? new URL(document.referrer).pathname : null,
      });
    });
  
    document.addEventListener(
      "click",
      function (event) {
        const el = event.target.closest(
          "button, a, input[type='submit'], input[type='button'], [data-log-action]"
        );
  
        if (!el) return;
  
        const info = elementInfo(el);
  
        send(info.data_log_action || "CLICK", {
          element: info,
          target_type: info.target_type,
          target_id: info.target_id,
        });
      },
      true
    );
  
    document.addEventListener(
      "submit",
      function (event) {
        const form = event.target;
        const fields = [];
  
        try {
          for (const el of form.elements) {
            if (!el.name) continue;
  
            const type = (el.type || "").toLowerCase();
  
            if (
              type === "password" ||
              type === "hidden" ||
              el.name.toLowerCase().includes("token") ||
              el.name.toLowerCase().includes("csrf")
            ) {
              continue;
            }
  
            fields.push({
              name: el.name,
              type: type || null,
              has_value: !!el.value,
            });
          }
        } catch (e) {}
  
        send(form.dataset.logAction || "FORM_SUBMIT", {
          form_id: form.id || null,
          form_name: form.getAttribute("name") || null,
          action_path: form.action ? new URL(form.action).pathname : null,
          method: form.method || null,
          target_type: form.dataset.targetType || null,
          target_id: form.dataset.targetId || null,
          fields: fields,
        });
      },
      true
    );
  
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") {
        send("PAGE_HIDDEN", {
          dwell_ms: Date.now() - startedAt,
        });
      }
    });
  
    window.addEventListener("error", function (event) {
      send("CLIENT_ERROR", {
        message: event.message || null,
        filename: event.filename || null,
        lineno: event.lineno || null,
        colno: event.colno || null,
      });
    });
  
    window.addEventListener("unhandledrejection", function (event) {
      send("CLIENT_PROMISE_REJECTION", {
        reason: event.reason ? String(event.reason).slice(0, 500) : null,
      });
    });
  })();

// Expose a simple manual logger for templates.
// This lets templates call:
// window.sessionLog.send("AMENDMENT_AI_GENERATION_REQUESTED", {...})
window.sessionLog = window.sessionLog || {};

window.sessionLog.send = window.sessionLog.send || function (action, details = {}) {
  try {
    const body = document.body || null;

    const eventId =
      details.event_id ||
      details.eventId ||
      (body ? body.dataset.eventId : null) ||
      null;

    const phase =
      details.phase ||
      (body ? body.dataset.phase : null) ||
      null;

    const payload = {
      action: action,
      page: window.location.pathname,
      event_id: eventId ? Number(eventId) : null,
      user_id: document.body?.dataset.userId
        ? Number(document.body.dataset.userId)
        : null,

      user_handle: document.body?.dataset.userHandle || null,
      phase: phase || null,
      target_type: details.target_type || null,
      target_id: details.target_id != null ? String(details.target_id) : null,
      details: Object.assign({
        title: document.title,
        url_path: window.location.pathname,
        url_query_keys: Array.from(new URLSearchParams(window.location.search).keys()),
        viewport: {
          width: window.innerWidth,
          height: window.innerHeight
        }
      }, details || {})
    };

    fetch("/api/session-log/client", {
      method: "POST",
      credentials: "same-origin",
      keepalive: true,
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json"
      },
      body: JSON.stringify(payload)
    }).catch(() => {});
  } catch (_) {
    // Logging must never break the UI.
  }
};