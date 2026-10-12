# pi-galley

Set [Pi](https://github.com/earendil-works/pi)'s chat in a readable measure on
wide terminals, with tool output, input, and footer free to run full width.

A galley is a long single column of set type. pi-galley keeps the transcript's
prose to a comfortable line length (the measure) while the parts of the screen
that read better wide, such as logs, diffs, and the status footer, keep the
whole terminal.

## Install

```sh
pi install npm:pi-galley
```

Restart Pi, or run `/reload` in existing sessions.

## Settings

| Command | Effect |
| --- | --- |
| `/galley 100` | Set the measure in cells (20 or more). |
| `/galley off` | Turn the galley off; everything runs full width. |
| `/galley left` | Set the galley flush left (ragged right). |
| `/galley center` | Center the galley. |
| `/galley tools full` | Run tool calls and `!` command output full width. |
| `/galley input full` | Run the input editor full width. |
| `/galley footer full` | Run the footer full width. |
| `/galley tools column` | Set tool output in the galley. Same for `input` and `footer`. |
| `/galley` | Show the current settings. |

Defaults: a 120-cell measure, flush left, with tool output, input, and footer
full width. Commands save to `pi-galley.json` in Pi's agent directory
(`~/.pi/agent` unless `PI_CODING_AGENT_DIR` points elsewhere).

Environment variables override the saved settings for one launch:

| Variable | Values |
| --- | --- |
| `PI_GALLEY_MEASURE` | `0` (off) or a whole number of 20 or more |
| `PI_GALLEY_ALIGN` | `left` or `center` |
| `PI_GALLEY_TOOLS` | `full` or `column` |
| `PI_GALLEY_INPUT` | `full` or `column` |
| `PI_GALLEY_FOOTER` | `full` or `column` |

## Wide diagrams

Mermaid diagrams wider than the measure break out to the width they need, so
Pi can still draw them. The prose around them stays in the galley.

## For extension authors

Extensions can cooperate with pi-galley without depending on it. Both hooks
use global symbols, so they do nothing when pi-galley is not installed.

- Set `component[Symbol.for("pi-galley.full-width")] = true` on a component
  your message or tool renderer returns. pi-galley leaves the transcript block
  that contains it at the full terminal width.
- Call `globalThis[Symbol.for("pi-galley.measure")]?.()` at render time for the
  current measure in cells. It returns `0` when the galley is off, and the
  function is absent when pi-galley is not loaded.

A chat-style extension can combine the two: run its block full width, cap its
bubbles at the measure, and put them against the left and right edges.

## Scope and limitations

- Terminals lay text out on a grid of equal-width cells, so pi-galley controls
  line length and placement, not fonts.
- The input setting covers the whole editor slot, including selectors and
  dialogs that replace the editor. Extension widgets above and below the editor
  stay in the galley.
- pi-galley wraps Pi's interactive components at render time. A Pi release
  that restructures those components can break it until pi-galley catches up.

## Credits

pi-galley began as a fork of Adam Chmara's
[pi-centered](https://github.com/ChmaraX/pi-centered), which contributed the
column rendering and the Mermaid breakout.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
