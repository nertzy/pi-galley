# Contributing

Thanks for helping improve pi-galley.

## Development

pi-galley needs Node.js 24 or later (enforced by `engines` in
[`package.json`](package.json)) and Python 3 for the end-to-end tests.
Linting uses Biome, markdownlint-cli2, and
[actionlint](https://github.com/rhysd/actionlint) 1.7.12; install `actionlint`
separately (on macOS, `brew install actionlint`).

```sh
npm ci
npm run check
npm run pack:check
```

`npm test` runs the real `pi` binary from the dev dependencies in a pseudo-terminal,
emulates the screen with [pyte](https://github.com/selectel/pyte), and checks
where every row lands. The first run creates a throwaway virtual environment
for pyte. Screens and logs go to `e2e/artifacts/`.

Run `npm run lint:fix` to apply the Biome and Markdown autofixes.

Try your checkout in Pi with `pi install /absolute/path/to/pi-galley`, then
`/reload` after each change.

Add or update tests for behavior changes. Pull requests should explain the problem, the chosen behavior, and any user-visible documentation changes.

## Reporting bugs and proposing changes

Use [GitHub issues](https://github.com/nertzy/pi-galley/issues) for reproducible bugs and focused proposals. For vulnerabilities, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
