// Test double for an extension that uses pi-galley's public contract without importing it.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Container } from "@earendil-works/pi-tui";

const FULL_WIDTH = Symbol.for("pi-galley.full-width");
const MEASURE = Symbol.for("pi-galley.measure");

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

export default function (pi: ExtensionAPI) {
  pi.registerMessageRenderer("galley-e2e", () => wrapped("MARKED"));
  pi.registerToolRenderer((toolName, next) =>
    toolName === "galley_e2e"
      ? {
          renderShell: "self",
          renderCall: () => new Container(),
          renderResult: () => wrapped("TOOLMARK"),
        }
      : next(),
  );
}
