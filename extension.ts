import { readFileSync, realpathSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  AssistantMessageComponent,
  BashExecutionComponent,
  CustomEditor,
  FooterComponent,
  getAgentDir,
  ToolExecutionComponent,
} from "@earendil-works/pi-coding-agent";
import {
  type Component,
  Container,
  Markdown,
  type MarkdownOptions,
  Marked,
  type TUI,
  type TuiMouseEvent,
  type TuiMouseEventResult,
} from "@earendil-works/pi-tui";

// Sets Pi's transcript in a galley: a column of at most `measure` cells, flush left or
// centered. Mermaid diagrams wider than the measure "break out" to the width they need
// (capped at the terminal) so Pi can still render them; surrounding text stays in the
// galley. Tool output, the input editor and the footer each run full width or sit in
// the galley.
//
//   /galley 120        set the measure (saved)
//   /galley off        full width (saved)
//   /galley left       flush left (saved)
//   /galley center     centered (saved)
//   /galley tools|input|footer full|column   that region full width or in the galley (saved)
//   /galley            show current
//
// Each setting: env var > saved value (<agent dir>/pi-galley.json) > default.
//   measure  PI_GALLEY_MEASURE  default 120
//   align    PI_GALLEY_ALIGN    default left
//   tools    PI_GALLEY_TOOLS    default full
//   input    PI_GALLEY_INPUT    default full
//   footer   PI_GALLEY_FOOTER   default full

const DEFAULT_MEASURE = 120;
const MIN_WIDTH = 20;
const CONFIG = join(getAgentDir(), "pi-galley.json");

/** 0 means off. Returns undefined for anything that is not 0 or an integer >= MIN_WIDTH. */
function parseMeasure(value: unknown): number | undefined {
  const n =
    value === "off"
      ? 0
      : typeof value === "string" && value.trim() !== ""
        ? Number(value)
        : value;
  return typeof n === "number" &&
    Number.isInteger(n) &&
    (n === 0 || n >= MIN_WIDTH)
    ? n
    : undefined;
}

type Align = "center" | "left";

function parseAlign(value: unknown): Align | undefined {
  return value === "center" || value === "left" ? value : undefined;
}

/** Where a region of the screen sits: at the terminal width, or in the galley. */
type Placement = "column" | "full";

function parsePlacement(value: unknown): Placement | undefined {
  return value === "column" || value === "full" ? value : undefined;
}

/** Regions that can run full width: tool calls and `!` output, the input editor, the footer. */
const REGIONS = ["tools", "input", "footer"] as const;
type Region = (typeof REGIONS)[number];

type Config = { measure: number; align: Align } & Record<Region, Placement>;

const placement = (region: string) => ({
  env: `PI_GALLEY_${region.toUpperCase()}`,
  parse: parsePlacement,
  hint: "column or full",
});

const SETTINGS: {
  [K in keyof Config]: {
    env: string;
    parse: (value: unknown) => Config[K] | undefined;
    hint: string;
  };
} = {
  measure: {
    env: "PI_GALLEY_MEASURE",
    parse: parseMeasure,
    hint: `0 or a whole number >= ${MIN_WIDTH}`,
  },
  align: { env: "PI_GALLEY_ALIGN", parse: parseAlign, hint: "center or left" },
  tools: placement("tools"),
  input: placement("input"),
  footer: placement("footer"),
};

const DEFAULTS: Config = {
  measure: DEFAULT_MEASURE,
  align: "left",
  tools: "full",
  input: "full",
  footer: "full",
};
const config: Config = { ...DEFAULTS };

const REGION_NAMES: Record<Region, string> = {
  tools: "tool output",
  input: "input",
  footer: "footer",
};

const label = () =>
  [
    config.measure ? `Measure: ${config.measure}` : "Measure: off",
    config.align === "left" ? "flush left" : "centered",
    ...REGIONS.map(
      (region) =>
        `${REGION_NAMES[region]} ${config[region] === "full" ? "full width" : "in galley"}`,
    ),
  ].join(", ");

function loadSaved(): Record<string, unknown> {
  try {
    const saved = JSON.parse(readFileSync(CONFIG, "utf8"));
    return saved && typeof saved === "object" ? saved : {};
  } catch {
    return {};
  }
}

/** Env var > saved value > default. Records the env value, or a warning when it is invalid. */
function resolve<K extends keyof Config>(
  key: K,
  saved: Record<string, unknown>,
  env: Partial<Config>,
  warnings: string[],
) {
  const { env: name, parse, hint } = SETTINGS[key];
  const raw = process.env[name];
  const fromEnv = raw === undefined ? undefined : parse(raw.trim());
  if (fromEnv !== undefined) env[key] = fromEnv;
  else if (raw !== undefined)
    warnings.push(`${name}="${raw}" ignored: use ${hint}`);
  config[key] = fromEnv ?? parse(saved[key]) ?? DEFAULTS[key];
}

/** Parse `/galley` arguments into the settings they change. */
function parseCommand(value: string): Partial<Config> | undefined {
  const measure = parseMeasure(value);
  if (measure !== undefined) return { measure };
  const align = parseAlign(value);
  if (align) return { align };
  const [name, arg, ...rest] = value.split(/\s+/);
  const region = REGIONS.find((candidate) => candidate === name);
  const where = region && !rest.length ? parsePlacement(arg) : undefined;
  return region && where ? { [region]: where } : undefined;
}

// ---------- Hooks (installed once per component, survive /reload) ----------
//
// Each Pi component gets thin trampolines that call whatever `state.impl` currently is.
// Every extension load installs its own impl, and session_shutdown removes it, so
// editing or removing this file takes effect on /reload.

type Render = (width: number) => string[];
type HandleMouse = (event: TuiMouseEvent) => TuiMouseEventResult | undefined;
interface Impl {
  render(comp: Hooked, width: number, render: Render): string[];
  handleMouse(
    comp: Hooked,
    event: TuiMouseEvent,
    handleMouse: HandleMouse,
  ): TuiMouseEventResult | undefined;
}

const STATE: unique symbol = Symbol.for("pi-galley.state.v1");
const HOOKED: unique symbol = Symbol.for("pi-galley.hooked.v1");
const PARTS: unique symbol = Symbol.for("pi-galley.parts.v1");
/** Set on the editor and footer containers; children inherit it as REGION. */
const REGION_ROOT: unique symbol = Symbol.for("pi-galley.region-root.v1");
const REGION: unique symbol = Symbol.for("pi-galley.region.v1");

// Public contract for other extensions (see README, "For extension authors").
/** Set to true on a rendered component to keep its transcript block full width. */
const FULL_WIDTH: unique symbol = Symbol.for("pi-galley.full-width");
/** A function on globalThis returning the measure in cells, or 0 when the galley is off. */
const MEASURE: unique symbol = Symbol.for("pi-galley.measure");

/** A Pi component plus the bookkeeping this extension attaches to it. */
interface Hooked extends Component {
  children?: Hooked[];
  child?: Hooked;
  [HOOKED]?: true;
  [PARTS]?: { key: string; parts: Part[] | null };
  [REGION_ROOT]?: Region;
  [REGION]?: Region;
  [FULL_WIDTH]?: boolean;
}

const published = globalThis as { [MEASURE]?: () => number };

const shared = globalThis as { [STATE]?: { impl?: Impl; tui?: TUI } };
shared[STATE] ??= {};
const state = shared[STATE];

function hook(comp: Hooked) {
  if (!comp || comp[HOOKED] || typeof comp.render !== "function") return;
  comp[HOOKED] = true;
  const render: Render = comp.render.bind(comp);
  comp.render = (width: number) =>
    state.impl ? state.impl.render(comp, width, render) : render(width);
  if (typeof comp.handleMouse === "function") {
    const handleMouse: HandleMouse = comp.handleMouse.bind(comp);
    comp.handleMouse = (event: TuiMouseEvent) =>
      state.impl
        ? state.impl.handleMouse(comp, event, handleMouse)
        : handleMouse(event);
  }
  if (typeof comp.invalidate === "function") {
    const invalidate = comp.invalidate.bind(comp);
    comp.invalidate = () => {
      delete comp[PARTS]; // theme or mermaid-mode change: rebuild split parts
      invalidate();
    };
  }
}

// ---------- Column geometry ----------

/** Column for content that needs `need` cells (0 = normal text). */
function column(width: number, need = 0): { w: number; pad: number } {
  const { measure, align } = config;
  if (!measure || width <= measure) return { w: width, pad: 0 };
  const w = Math.min(width, Math.max(measure, need));
  const left = align === "left" ? 0 : Math.floor((width - measure) / 2);
  // Wide blocks keep the column's left edge when they fit, else shift left.
  return { w, pad: Math.min(left, width - w) };
}

/** Prompt-zone markers (OSC 133) that Pi puts at the start of a line. */
// biome-ignore lint/suspicious/noControlCharactersInRegex: OSC 133 markers are escape sequences.
const OSC_PREFIX = /^(?:\x1b\]133;[A-Z]\x07)*/;

function indent(lines: string[], pad: number): string[] {
  if (!pad) return lines;
  const spaces = " ".repeat(pad);
  return lines.map((line) => {
    const osc = OSC_PREFIX.exec(line)?.[0] ?? ""; // prompt-zone markers stay at column 0
    return osc + spaces + line.slice(osc.length);
  });
}

// ---------- Mermaid breakout ----------

let renderMermaid: ((src: string) => { width: number } | undefined) | undefined;

async function loadMermaid(): Promise<boolean> {
  if (renderMermaid) return true;
  try {
    // grok-mermaid is Pi's own dependency; resolve it from Pi's install.
    const req = createRequire(realpathSync(process.argv[1] ?? ""));
    const mod = await import(pathToFileURL(req.resolve("grok-mermaid")).href);
    if (typeof mod.render === "function") renderMermaid = mod.render;
  } catch {}
  return !!renderMermaid;
}

const artWidths = new Map<string, number>();

function artWidth(src: string): number {
  let width = artWidths.get(src);
  if (width === undefined) {
    try {
      width = renderMermaid?.(src)?.width ?? 0;
    } catch {
      width = 0;
    }
    if (artWidths.size > 200) artWidths.clear();
    artWidths.set(src, width);
  }
  return width;
}

/** Same test Pi uses (components/mermaid.js). */
function isMermaid(token: {
  type: string;
  lang?: string;
}): token is { type: "code"; lang: string; text: string; raw: string } {
  return (
    token.type === "code" &&
    token.lang?.trim().split(/\s+/, 1)[0]?.toLowerCase() === "mermaid"
  );
}

/** Markdown's private fields, read to rebuild a block in parts with the same settings. */
interface MarkdownFields {
  text: unknown;
  paddingX?: number;
  paddingY?: number;
  theme: ConstructorParameters<typeof Markdown>[3];
  defaultTextStyle?: ConstructorParameters<typeof Markdown>[4];
  options?: MarkdownOptions;
}

const fieldsOf = (md: Markdown) => md as unknown as MarkdownFields;

/** Ask Pi's own transform pipeline whether it would draw this block at `width` (mermaid mode, streaming). */
function piWillDraw(md: MarkdownFields, raw: string, width: number): boolean {
  const transform = md.options?.transform;
  if (typeof transform !== "function") return false;
  const out = transform(raw, width);
  return typeof out === "string" && !out.startsWith(raw.trimEnd());
}

type Part = { md: Markdown; need: number }; // need 0 = column width

const lexer = new Marked();

/** Split a Markdown block into text parts and wide-diagram parts; null = render normally. */
function buildParts(md: MarkdownFields): Part[] | null {
  const text = md.text;
  const { measure } = config;
  if (
    !renderMermaid ||
    !measure ||
    md.paddingY !== 0 ||
    typeof text !== "string"
  )
    return null;
  const padX = md.paddingX ?? 0;
  const pieces: { src: string; need: number }[] = [];
  let cursor = 0;
  // Top-level tokens only, like Pi: diagrams nested in lists, quotes or longer fences stay put.
  for (const token of lexer.lexer(text)) {
    if (!isMermaid(token)) continue;
    const art = artWidth(token.text);
    const need = art + padX * 2;
    if (!art || need <= measure || !piWillDraw(md, token.raw, art)) continue;
    const at = text.indexOf(token.raw, cursor);
    if (at < 0) return null;
    pieces.push(
      { src: text.slice(cursor, at), need: 0 },
      { src: token.raw, need },
    );
    cursor = at + token.raw.length;
  }
  if (!pieces.length) return null;
  pieces.push({ src: text.slice(cursor), need: 0 });
  return pieces
    .map((piece) => ({ ...piece, src: piece.src.replace(/^\n+|\n+$/g, "") }))
    .filter((piece) => piece.src.trim())
    .map((piece) => ({
      md: new Markdown(
        piece.src,
        padX,
        0,
        md.theme,
        md.defaultTextStyle,
        md.options,
      ),
      need: piece.need,
    }));
}

function renderSplit(comp: Hooked & Markdown, width: number): string[] | null {
  const md = fieldsOf(comp);
  const key = `${config.measure}\0${md.text}`;
  if (comp[PARTS]?.key !== key) comp[PARTS] = { key, parts: buildParts(md) };
  const parts = comp[PARTS]?.parts;
  if (!parts) return null;
  const lines: string[] = [];
  for (const part of parts) {
    const { w, pad } = column(width, part.need);
    if (lines.at(-1)?.trim()) lines.push("");
    lines.push(...indent(part.md.render(w), pad));
  }
  return lines;
}

// ---------- Impl ----------

/** Containers we descend into; everything else is set in the galley as one block. */
function isWalked(comp: Hooked): boolean {
  return (
    Object.getPrototypeOf(comp) === Container.prototype ||
    comp instanceof AssistantMessageComponent
  );
}

/** The region a component belongs to, if it can run full width. */
function regionOf(comp: Hooked): Region | undefined {
  if (
    comp instanceof ToolExecutionComponent ||
    comp instanceof BashExecutionComponent
  )
    return "tools";
  return comp[REGION];
}

/**
 * How deep to look for a FULL_WIDTH mark. A tool renderer's output sits four levels
 * down: tool component, render container, MouseRegion, then the renderer's root.
 */
const MARK_DEPTH = 6;

/** Containers hold `children`; single-child wrappers such as MouseRegion hold a private `child`. */
const innerOf = (comp: Hooked): Hooked[] =>
  comp.child ? [comp.child] : (comp.children ?? []);

function isMarkedFullWidth(comp: Hooked, depth = MARK_DEPTH): boolean {
  return (
    comp[FULL_WIDTH] === true ||
    (depth > 0 &&
      innerOf(comp).some((inner) => isMarkedFullWidth(inner, depth - 1)))
  );
}

function isFullWidth(comp: Hooked): boolean {
  const region = regionOf(comp);
  return (
    (region !== undefined && config[region] === "full") ||
    isMarkedFullWidth(comp)
  );
}

/** Pi's renderers expose the focused component; the TUI interface does not declare it. */
type Focusing = TUI & { getFocusedComponent?(): Component | null };

/**
 * Tag the containers that hold the input editor and the footer. Pi swaps their children
 * (selectors and dialogs replace the editor, extensions replace the footer), so the
 * region travels from the container to whatever child it holds at render time.
 */
function tagRegions(tui: TUI | undefined) {
  const roots: Hooked[] = tui?.children ?? [];
  const holding = (test: (child: Component) => boolean) =>
    roots.find((root) => root.children?.some(test));
  const focused = (tui as Focusing | undefined)?.getFocusedComponent?.();
  const editor = holding(
    (child) => child instanceof CustomEditor || child === focused,
  );
  const footer =
    holding((child) => child instanceof FooterComponent) ?? roots.at(-1);
  if (editor) editor[REGION_ROOT] = "input";
  if (footer && footer !== editor) footer[REGION_ROOT] = "footer";
}

const impl: Impl = {
  render(comp, width, render) {
    if (isWalked(comp)) {
      const region = comp[REGION_ROOT] ?? comp[REGION];
      for (const child of comp.children ?? []) {
        hook(child); // children change as messages arrive
        child[REGION] = region;
      }
      return render(width);
    }
    if (comp instanceof Markdown) {
      const lines = renderSplit(comp, width);
      if (lines) return lines;
    }
    if (isFullWidth(comp)) return render(width);
    const { w, pad } = column(width);
    return indent(render(w), pad);
  },
  handleMouse(comp, event, handleMouse) {
    if (isWalked(comp) || isFullWidth(comp)) return handleMouse(event);
    const { w, pad } = column(event.width);
    if (event.x < pad || event.x >= pad + w) return undefined;
    return handleMouse({ ...event, x: event.x - pad, width: w });
  },
};

const measure = () => config.measure;

// ---------- Extension ----------

export default function (pi: ExtensionAPI) {
  const saved = loadSaved();
  const env: Partial<Config> = {};
  const badEnv: string[] = [];
  for (const key of Object.keys(SETTINGS) as (keyof Config)[])
    resolve(key, saved, env, badEnv);

  pi.on("session_start", async (_event, ctx) => {
    if (ctx.mode !== "tui") return;
    for (const warning of badEnv) ctx.ui.notify(warning, "warning");
    if (!(await loadMermaid())) {
      ctx.ui.notify(
        "pi-galley: grok-mermaid not found; wide diagrams stay in the galley",
        "warning",
      );
    }
    // Borrow the TUI via a throwaway widget (its factory runs synchronously), then remove it.
    ctx.ui.setWidget("__pi-galley", (tui) => {
      state.tui = tui as TUI;
      return { render: () => [], invalidate() {} };
    });
    ctx.ui.setWidget("__pi-galley", undefined);
    tagRegions(state.tui);
    state.impl = impl;
    published[MEASURE] = measure;
    for (const child of state.tui?.children ?? []) hook(child);
    state.tui?.requestRender(true);
  });

  pi.on("session_shutdown", async () => {
    if (state.impl !== impl) return; // a newer load already took over
    state.impl = undefined;
    if (published[MEASURE] === measure) delete published[MEASURE];
    try {
      state.tui?.requestRender(true);
    } catch {}
  });

  const usage = `Usage: /galley <whole number >= ${MIN_WIDTH}> | off | left | center | tools|input|footer full|column`;

  pi.registerCommand("galley", {
    description: `Set the measure, alignment, or which regions run full width: /galley <n >= ${MIN_WIDTH}> | off | left | center | tools|input|footer full|column (saved). No arg shows current.`,
    handler: async (arg, ctx) => {
      const value = (arg ?? "").trim();
      if (!value) {
        ctx.ui.notify(label(), "info");
        return;
      }
      const change = parseCommand(value);
      if (!change) {
        ctx.ui.notify(usage, "error");
        return;
      }
      let saved = true;
      try {
        writeFileSync(
          CONFIG,
          `${JSON.stringify({ ...loadSaved(), ...change }, null, 2)}\n`,
        );
      } catch (error) {
        saved = false;
        ctx.ui.notify(
          `Could not save ${CONFIG}: ${(error as Error).message}`,
          "error",
        );
      }
      Object.assign(config, change);
      state.tui?.requestRender(true);
      const overridden = (Object.keys(change) as (keyof Config)[])
        .filter((key) => env[key] !== undefined)
        .map((key) => `${SETTINGS[key].env}=${env[key]}`);
      const note = !saved
        ? " (this session only)"
        : overridden.length
          ? ` (saved; ${overridden.join(", ")} still wins on next launch)`
          : " (saved)";
      ctx.ui.notify(label() + note, "info");
    },
  });
}
