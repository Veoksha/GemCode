# Install and First Run

## Requirements
- Python 3.11 or newer
- A Google Gemini API key
- A working shell environment with permission to read your target project directory

## Local editable install
From the repository:

```bash
cd gemcode
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
```

Optional extras:

```bash
python3 -m pip install -e ".[mcp]"
```

## API key setup
GemCode expects a Gemini API key in the environment or a `.env` file.

Common options:

```bash
export GOOGLE_API_KEY="your-key"
```

or use the interactive credential helper:

```bash
gemcode login
```

GemCode also reads `.env` files via `load_cli_environment()` in `gemcode/src/gemcode/config.py`.

## Optional: web or custom UI
GemCode includes a built-in HTTP API. UIs (official app, your own dashboard, editor plugin) are **thin clients** that call it — they are not required to use the CLI.

```bash
gemcode serve -C /path/to/project
```

Defaults to `http://127.0.0.1:3001`. If that port is busy, GemCode will automatically pick the next available port and print the URL to connect to. From an interactive session you can also run `/serve` to start the API in the background.

See [`web-ui-contract.md`](web-ui-contract.md) for routes and streaming format.

## First run flow
On the first interactive run, GemCode may prompt for:

1. **Workspace trust**
2. **API key**
3. **Initial project state creation**

Relevant code paths:
- trust prompt: `gemcode/src/gemcode/cli.py`
- trust storage: `gemcode/src/gemcode/trust.py`
- `.gemcode/` initialization: `gemcode/src/gemcode/cli.py`

## Recommended first command
Run from the project you want GemCode to operate on:

```bash
gemcode -C /path/to/project
```

Using `-C` is strongly recommended. It ensures:
- the correct project root
- correct `.gemcode/` state placement
- correct trust scope
- correct instruction file loading

## Non-interactive environments
For CI or scripts:
- set `GOOGLE_API_KEY` explicitly
- do not rely on interactive trust prompts
- pass a prompt or pipe one on stdin

Examples:

```bash
gemcode -C /path/to/project "Explain the architecture"
printf '%s\n' "Summarize failing tests" | gemcode -C /path/to/project
```

## Upgrade workflow

### Editable install from source
If you already installed in editable mode:

```bash
cd gemcode
python3 -m pip install -e .
```

### Published package upgrade

```bash
python3 -m pip install -U gemcode
```

## Troubleshooting

### `argument -C/--directory: expected one argument`
You passed `-C` without a path. Use:

```bash
gemcode -C .
```

or:

```bash
gemcode -C "/absolute/path/to/project"
```

### Folder permission problems on macOS
If GemCode says the terminal cannot access the folder:
- grant Terminal access to Desktop/Documents in system privacy settings
- or move the repository to an accessible location

### Wrong binary/version
Check which binary is running:

```bash
which gemcode
gemcode version
```

This matters if the repo code is newer than the installed package in your Python user bin directory.
