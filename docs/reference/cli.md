# CLI Reference

The EDAC CLI provides commands for interacting with the server, managing tasks, agents, events, approvals, and webhooks.

## Installation

The CLI is installed automatically with the `edac` package:

```bash
pip install edac
```

## Usage

```bash
edac --version
edac status
edac tasks submit --goal "write a haiku"
edac events follow --topics agent.results
```

## CLI Groups

| Group | Commands |
|-------|----------|
| `tasks` | `submit`, `batch`, `list`, `get`, `cancel`, `events` |
| `agents` | `list`, `create`, `delete` |
| `events` | `follow`, `watch` |
| `webhooks` | `register`, `list`, `delete` |

## Global Options

| Option | Description |
|--------|-------------|
| `--config, -c` | Path to JSON config file |
| `--verbose, -v` | Enable verbose/debug output |
| `--version` | Show version and exit |
