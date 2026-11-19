// Test double for an extension that uses pi-galley's public contract without importing it.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Container } from "@earendil-works/pi-tui";

const FULL_WIDTH = Symbol.for("pi-galley.full-width");
const MEASURE = Symbol.for("pi-galley.measure");
const IMAGE_WIDTH = Symbol.for("pi-galley.image-width");

/** `/galley-e2e-hints off` withdraws every image-width hint; `on` restores them. */
let hinting = true;

/** Two lines against the left and right edges; the right one reports the measure it read. */
function marked(tag: string) {
  return {
    [FULL_WIDTH]: true,
    render(width: number) {
      const read = (globalThis as { [MEASURE]?: () => number })[MEASURE];
      const right = `${tag}-RIGHT measure=${read ? read() : "unset"}`;
      return [
        `${tag}-LEFT`,
        `${" ".repeat(Math.max(0, width - right.length))}${right}`,
      ];
    },
    invalidate() {},
  };
}

/** Wraps the mark in a container, as real renderers do. */
function wrapped(tag: string) {
  const container = new Container();
  container.addChild(marked(tag));
  return container;
}

/** What a `galley_e2e_image` call asks of its result block. */
type ImageCall = { label: string; width?: unknown; fullWidth?: boolean };

/** One line naming the call, hinting the image width the call asks for. */
function hinted({ label, width, fullWidth }: ImageCall) {
  return {
    [FULL_WIDTH]: fullWidth === true,
    get [IMAGE_WIDTH]() {
      return hinting ? width : undefined;
    },
    render: () => [`IMAGE-${label}`],
    invalidate() {},
  };
}

export default function (pi: ExtensionAPI) {
  pi.registerCommand("galley-e2e-hints", {
    description: "e2e: /galley-e2e-hints on|off",
    handler: async (arg, ctx) => {
      hinting = arg.trim() !== "off";
      ctx.ui.notify(`IMAGE-HINTS ${hinting ? "on" : "off"}`, "info");
    },
  });
  pi.registerMessageRenderer("galley-e2e", () => wrapped("MARKED"));
  pi.registerToolRenderer((toolName, next) =>
    toolName === "galley_e2e"
      ? {
          renderShell: "self",
          renderCall: () => new Container(),
          renderResult: () => wrapped("TOOLMARK"),
        }
      : toolName === "galley_e2e_image"
        ? {
            renderCall: () => new Container(),
            renderResult: (_result, _options, _theme, context) =>
              hinted(context.args as ImageCall),
          }
        : next(),
  );
}
