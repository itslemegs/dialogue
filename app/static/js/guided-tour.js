(function () {
    "use strict";

    // English fallback keeps the shared engine usable without the page catalogue.
    function tr(key, fallback, params = {}) {
      if (window.UII18n && typeof window.UII18n.t === "function") {
        return window.UII18n.t(key, params);
      }
      return fallback.replace(/\{(\w+)\}/g, (match, name) =>
        Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match);
    }

    let steps = [];
    let currentIndex = 0;
    let active = false;

    let overlay = null;
    let spotlight = null;
    let popover = null;

    function ensureUI() {
      if (overlay) return;

      overlay = document.createElement("div");
      overlay.id = "dialogue-tour-overlay";
      overlay.className =
        "fixed inset-0 z-[9990] hidden";

      spotlight = document.createElement("div");
      spotlight.id = "dialogue-tour-spotlight";
      spotlight.className =
        "fixed z-[9991] rounded-xl ring-4 ring-white pointer-events-none hidden";

        spotlight.style.boxShadow =
        "0 0 0 9999px rgba(15, 23, 42, 0.62), 0 18px 45px rgba(15, 23, 42, 0.25)";
      spotlight.style.transition =
        "top .22s ease, left .22s ease, width .22s ease, height .22s ease";

      popover = document.createElement("div");
      popover.id = "dialogue-tour-popover";
      popover.className =
        "fixed z-[9992] w-[min(22rem,calc(100vw-2rem))] bg-white rounded-2xl shadow-2xl border border-slate-200 hidden";

      popover.innerHTML = `
        <div class="p-5">
          <div class="flex items-start justify-between gap-4">
            <div>
              <div
                id="dialogue-tour-step"
                class="text-xs font-semibold uppercase tracking-wide text-indigo-600 mb-1"
              ></div>

              <h2
                id="dialogue-tour-title"
                class="text-lg font-semibold text-slate-900"
              ></h2>
            </div>

            <button
              type="button"
              id="dialogue-tour-close"
              class="text-slate-400 hover:text-slate-700 text-xl leading-none"
              aria-label=""
            >
              ×
            </button>
          </div>

          <p
            id="dialogue-tour-text"
            class="mt-2 text-sm leading-6 text-slate-600"
          ></p>

          <div class="flex items-center justify-between gap-3 mt-5">
            <button
              type="button"
              id="dialogue-tour-skip"
              class="text-sm text-slate-500 hover:text-slate-800"
            >

            </button>

            <div class="flex gap-2">
              <button
                type="button"
                id="dialogue-tour-back"
                class="px-3 py-2 text-sm border border-slate-300 rounded-lg text-slate-700 hover:bg-slate-50"
              >

              </button>

              <button
                type="button"
                id="dialogue-tour-next"
                class="px-4 py-2 text-sm rounded-lg bg-indigo-600 text-white hover:bg-indigo-700"
              >

              </button>
            </div>
          </div>
        </div>
      `;

      popover.querySelector("#dialogue-tour-close").setAttribute(
        "aria-label", tr("tutorial.common.close", "Close tutorial"));
      popover.querySelector("#dialogue-tour-skip").textContent =
        tr("tutorial.common.skip", "Skip tutorial");
      popover.querySelector("#dialogue-tour-back").textContent =
        tr("tutorial.common.back", "Back");

      document.body.appendChild(overlay);
      document.body.appendChild(spotlight);
      document.body.appendChild(popover);

      popover
        .querySelector("#dialogue-tour-close")
        .addEventListener("click", finish);

      popover
        .querySelector("#dialogue-tour-skip")
        .addEventListener("click", finish);

      popover
        .querySelector("#dialogue-tour-back")
        .addEventListener("click", previous);

      popover
        .querySelector("#dialogue-tour-next")
        .addEventListener("click", next);

      window.addEventListener("resize", () => {
        if (active) renderStep();
      });

      window.addEventListener(
        "scroll",
        () => {
          if (active) positionCurrentStep();
        },
        true
      );

      document.addEventListener("keydown", (event) => {
        if (!active) return;

        if (event.key === "Escape") {
          finish();
        } else if (event.key === "ArrowRight") {
          next();
        } else if (event.key === "ArrowLeft") {
          previous();
        }
      });
    }

    function visibleSteps(inputSteps) {
      return inputSteps.filter((step) => {
        if (!step || !step.target) return false;

        const target = document.querySelector(step.target);
        if (!target) return false;

        const style = window.getComputedStyle(target);

        return (
          style.display !== "none" &&
          style.visibility !== "hidden"
        );
      });
    }

    function start(inputSteps, options = {}) {
      ensureUI();

      steps = visibleSteps(inputSteps || []);

      if (!steps.length) return;

      currentIndex = 0;
      active = true;

      overlay.classList.remove("hidden");
      spotlight.classList.remove("hidden");
      popover.classList.remove("hidden");

      if (options.storageKey) {
        popover.dataset.storageKey = options.storageKey;
      } else {
        delete popover.dataset.storageKey;
      }

      renderStep();
    }

    function finish() {
      if (!active) return;

      active = false;

      overlay?.classList.add("hidden");
      spotlight?.classList.add("hidden");
      popover?.classList.add("hidden");

      const storageKey = popover?.dataset.storageKey;

      if (storageKey) {
        try {
          localStorage.setItem(storageKey, "completed");
        } catch (_) {}
      }
    }

    function next() {
      if (!active) return;

      if (currentIndex >= steps.length - 1) {
        finish();
        return;
      }

      currentIndex += 1;
      renderStep();
    }

    function previous() {
      if (!active || currentIndex === 0) return;

      currentIndex -= 1;
      renderStep();
    }

    function renderStep() {
      const step = steps[currentIndex];
      if (!step) return finish();

      const target = document.querySelector(step.target);
      if (!target) {
        steps.splice(currentIndex, 1);

        if (!steps.length) {
          finish();
          return;
        }

        if (currentIndex >= steps.length) {
          currentIndex = steps.length - 1;
        }

        renderStep();
        return;
      }

      const rect = target.getBoundingClientRect();

        const alreadyVisible =
        rect.top >= 80 &&
        rect.bottom <= window.innerHeight - 40;

        if (!alreadyVisible) {
        target.scrollIntoView({
            behavior: "smooth",
            block: "center",
            inline: "nearest",
        });
        }

      popover.querySelector("#dialogue-tour-step").textContent =
        tr("tutorial.common.step", "Step {current} of {total}", {current: currentIndex + 1, total: steps.length});

      popover.querySelector("#dialogue-tour-title").textContent =
        step.title || "";

      popover.querySelector("#dialogue-tour-text").textContent =
        step.text || "";

      const backButton =
        popover.querySelector("#dialogue-tour-back");

      backButton.disabled = currentIndex === 0;
      backButton.classList.toggle(
        "opacity-40",
        currentIndex === 0
      );

      popover.querySelector("#dialogue-tour-next").textContent =
        currentIndex === steps.length - 1
          ? tr("tutorial.common.done", "Done")
          : tr("tutorial.common.next", "Next");

      window.setTimeout(positionCurrentStep, 260);
    }

    function positionCurrentStep() {
      const step = steps[currentIndex];
      if (!step) return;

      const target = document.querySelector(step.target);
      if (!target) return;

      const rect = target.getBoundingClientRect();

      const padding =
        typeof step.padding === "number"
          ? step.padding
          : 8;

      spotlight.style.top =
        `${Math.max(4, rect.top - padding)}px`;

      spotlight.style.left =
        `${Math.max(4, rect.left - padding)}px`;

      spotlight.style.width =
        `${Math.min(
          window.innerWidth - 8,
          rect.width + padding * 2
        )}px`;

      spotlight.style.height =
        `${Math.min(
          window.innerHeight - 8,
          rect.height + padding * 2
        )}px`;

      positionPopover(rect, step.placement || "auto");
    }

    function positionPopover(rect, placement) {
      const gap = 18;
      const margin = 16;

      const popRect = popover.getBoundingClientRect();

      let top;
      let left;

      const belowFits =
        rect.bottom + gap + popRect.height <
        window.innerHeight - margin;

      const aboveFits =
        rect.top - gap - popRect.height > margin;

      if (
        placement === "bottom" ||
        (placement === "auto" && belowFits)
      ) {
        top = rect.bottom + gap;
        left =
          rect.left +
          rect.width / 2 -
          popRect.width / 2;
      } else if (
        placement === "top" ||
        (placement === "auto" && aboveFits)
      ) {
        top = rect.top - popRect.height - gap;
        left =
          rect.left +
          rect.width / 2 -
          popRect.width / 2;
      } else {
        top = Math.max(
          margin,
          Math.min(
            rect.top,
            window.innerHeight -
              popRect.height -
              margin
          )
        );

        if (
          rect.right + gap + popRect.width <
          window.innerWidth - margin
        ) {
          left = rect.right + gap;
        } else {
          left = rect.left - popRect.width - gap;
        }
      }

      left = Math.max(
        margin,
        Math.min(
          left,
          window.innerWidth -
            popRect.width -
            margin
        )
      );

      top = Math.max(
        margin,
        Math.min(
          top,
          window.innerHeight -
            popRect.height -
            margin
        )
      );

      popover.style.top = `${top}px`;
      popover.style.left = `${left}px`;
    }

    function completed(storageKey) {
      try {
        return (
          localStorage.getItem(storageKey) ===
          "completed"
        );
      } catch (_) {
        return false;
      }
    }

    function reset(storageKey) {
      try {
        localStorage.removeItem(storageKey);
      } catch (_) {}
    }

    window.DialogueTour = {
      start,
      finish,
      completed,
      reset,
    };
  })();