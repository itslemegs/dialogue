(function () {
  "use strict";

  const uiLocale = document.documentElement.lang === "ja" ? "ja-JP" : "sv-SE";

  const JST_OPTIONS = {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  };

  window.formatJstTimestamp = function (value, localized = false) {
    if (value === null || value === undefined || value === "") {
      return "";
    }

    let dt;

    if (value instanceof Date) {
      dt = value;
    } else {
      const raw = String(value).trim();

      // Database/API timestamps in this application may be naive UTC.
      // Normalize them as UTC before converting them for display.
      let normalized = raw.replace(" ", "T");

      // JavaScript reliably handles milliseconds; trim longer
      // Python-style fractional seconds if necessary.
      normalized = normalized.replace(
        /(\.\d{3})\d+(?=Z|[+-]\d{2}:\d{2}|$)/,
        "$1"
      );

      if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(normalized)) {
        normalized += "Z";
      }

      dt = new Date(normalized);
    }

    if (Number.isNaN(dt.getTime())) {
      return String(value);
    }

    return dt.toLocaleString(localized ? uiLocale : "sv-SE", JST_OPTIONS) + " JST";
  };
})();
