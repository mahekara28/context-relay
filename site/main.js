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
    image: "thread-map.png",
    alt: "Actual local thread map with four synthetic harness sessions.",
    description:
      "See indexed sessions from supported coding harnesses on one local canvas.",
  },
  review: {
    image: "thread-review.png",
    alt: "Actual review drawer for a Claude Code to Codex connection.",
    description:
      "Choose a destination and review the goal and source messages before connecting.",
  },
  handoff: {
    image: "thread-connected.png",
    alt: "Actual thread map showing a queued connection and its delivery status.",
    description:
      "See whether the handoff is saved, sent, or acknowledged by the receiving agent.",
  },
};
const tourButtons = $$("[data-tour]");
function selectTour(button) {
  const key = button.dataset.tour;
  tourButtons.forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab === button));
    tab.tabIndex = tab === button ? 0 : -1;
  });
  $("#tour-image").src = `./assets/${tours[key].image}`;
  $("#tour-image").alt = tours[key].alt;
  $("#tour-description").textContent = tours[key].description;
  $("#tour-fullscreen").href = `./assets/${tours[key].image}`;
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
