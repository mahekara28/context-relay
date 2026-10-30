// Static, local interactions only. No analytics, model calls or localhost access.
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const systemTheme = matchMedia("(prefers-color-scheme: dark)");
const themeButton = $("#theme-toggle");
let savedTheme;
try {
  savedTheme = localStorage.getItem("relay-site-theme");
} catch {
  /* Storage may be disabled. */
}
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  themeButton.setAttribute(
    "aria-label",
    `Switch to ${theme === "dark" ? "light" : "dark"} theme`,
  );
  $('meta[name="theme-color"]').content =
    theme === "dark" ? "#171d1a" : "#f6f5f1";
}
setTheme(
  ["dark", "light"].includes(savedTheme)
    ? savedTheme
    : systemTheme.matches
      ? "dark"
      : "light",
);
themeButton.addEventListener("click", () => {
  savedTheme =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  setTheme(savedTheme);
  try {
    localStorage.setItem("relay-site-theme", savedTheme);
  } catch {
    /* In-memory choice still works. */
  }
});
systemTheme.addEventListener("change", () => {
  if (!savedTheme) setTheme(systemTheme.matches ? "dark" : "light");
});

let toastTimeout;
function announce(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => toast.classList.remove("visible"), 2400);
}
const copyDialog = $("#copy-dialog");
const installCommands = {
  npm: "npm install --global @mahekara28/context-relay@beta\nrelay demo\nrelay ui",
  npx: "npx @mahekara28/context-relay@beta demo\nnpx @mahekara28/context-relay@beta ui",
};
$$("[data-install]").forEach((button) =>
  button.addEventListener("click", () => {
    $$("[data-install]").forEach((choice) =>
      choice.setAttribute("aria-pressed", String(choice === button)),
    );
    $("#install-command").textContent = installCommands[button.dataset.install];
  }),
);
$$("[data-copy]").forEach((button) =>
  button.addEventListener("click", async () => {
    const command = document
      .getElementById(button.dataset.copy)
      .textContent.trim();
    try {
      await navigator.clipboard.writeText(command);
      announce("Command copied. Run it in your terminal.");
    } catch {
      const field = copyDialog.querySelector("textarea");
      field.value = command;
      copyDialog.showModal();
      field.focus();
      field.select();
    }
  }),
);
$("#close-copy-dialog").addEventListener("click", () => copyDialog.close());

const tours = {
  threads: {
    alt: "Actual Context Relay workspace showing captured Claude Code, Codex and Goose example sessions.",
    description:
      "Browse captured threads and select the source evidence your next session needs.",
  },
  review: {
    alt: "Actual checkpoint editor with a reviewed seven-day retention correction and source citations.",
    description:
      "Review the goal, current instructions and open work beside their source evidence.",
  },
  handoff: {
    alt: "Actual Context Relay handoff view with a continuation prompt, delivery state and Markdown export.",
    description:
      "Offer the checkpoint to a destination and inspect its packet, receipt and continuation prompt.",
  },
};
const tourButtons = $$("[data-tour]");
function selectTour(button) {
  const key = button.dataset.tour;
  tourButtons.forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab === button));
    tab.tabIndex = tab === button ? 0 : -1;
  });
  $("#tour-image").src = `./assets/${key}.png`;
  $("#tour-image").alt = tours[key].alt;
  $("#tour-description").textContent = tours[key].description;
  $("#tour-fullscreen").href = `./assets/${key}.png`;
  $("#tour-panel").setAttribute("aria-labelledby", button.id);
}
tourButtons.forEach((button, index) => {
  button.addEventListener("click", () => selectTour(button));
  button.addEventListener("keydown", (event) => {
    const next =
      event.key === "ArrowRight"
        ? (index + 1) % 3
        : event.key === "ArrowLeft"
          ? (index + 2) % 3
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? 2
              : null;
    if (next === null) return;
    event.preventDefault();
    selectTour(tourButtons[next]);
    tourButtons[next].focus();
  });
});

$$("[data-harness]").forEach((button) =>
  button.addEventListener("click", () => {
    $$("[data-harness]").forEach((choice) =>
      choice.setAttribute("aria-pressed", String(choice === button)),
    );
    $("#connect-command").textContent =
      `relay project add --root /absolute/path/to/project\nrelay connect --project PROJECT_ID --harness ${button.dataset.harness}\nrelay ui`;
  }),
);

const motionPreference = matchMedia("(prefers-reduced-motion: reduce)");
let playing = !motionPreference.matches;
let stage = 0;
let destination = "Codex";
let visible = true;
let timer;
const flowButton = $("#flow-toggle");
const stages = [
  ["01 / capture source events", "Preserve the correction and its source."],
  ["02 / review a checkpoint", "Choose the current instruction and open work."],
  ["03 / offer the reviewed context", ""],
];
function showStage(next) {
  stage = next;
  $(".context-packet").dataset.stage = String(stage);
  $("#handoff-example").dataset.activeStage = String(stage);
  $("#flow-label").textContent = stages[stage][0];
  $("#flow-caption").textContent =
    stage === 2
      ? `${destination} can inspect, accept and continue.`
      : stages[stage][1];
  $("#delivery-label").textContent =
    stage === 2
      ? `MCP · ${destination} / fresh thread`
      : "MCP · inspect · accept · continue";
  $$(".step-dots button").forEach((button) =>
    button.setAttribute(
      "aria-pressed",
      String(Number(button.dataset.stage) === stage),
    ),
  );
}
function updatePlayback() {
  clearInterval(timer);
  flowButton.textContent = playing ? "Pause" : "Play";
  flowButton.setAttribute(
    "aria-label",
    `${playing ? "Pause" : "Play"} handoff animation`,
  );
  if (playing && visible && !document.hidden)
    timer = setInterval(() => showStage((stage + 1) % 3), 4000);
}
flowButton.addEventListener("click", () => {
  playing = !playing;
  updatePlayback();
});
$$(".step-dots button").forEach((button) =>
  button.addEventListener("click", () => {
    playing = false;
    showStage(Number(button.dataset.stage));
    updatePlayback();
  }),
);
$$("[data-target]").forEach((button) =>
  button.addEventListener("click", () => {
    $$("[data-target]").forEach((choice) =>
      choice.setAttribute("aria-pressed", String(choice === button)),
    );
    destination = button.textContent.trim();
    showStage(2);
    playing = false;
    updatePlayback();
  }),
);
motionPreference.addEventListener("change", () => {
  playing = !motionPreference.matches;
  updatePlayback();
});
document.addEventListener("visibilitychange", updatePlayback);
new IntersectionObserver(([entry]) => {
  visible = entry.isIntersecting;
  updatePlayback();
}).observe($("#handoff-example"));
showStage(0);
updatePlayback();
