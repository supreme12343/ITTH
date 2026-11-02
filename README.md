<div align="center">

# ITTH
### A toolbox for learning, building, and presenting

**One workspace. A growing collection of practical AI tools.**

[![Private repository](https://img.shields.io/badge/repository-private-6C63FF?style=for-the-badge)](#)
[![Python](https://img.shields.io/badge/Python-apps-3776AB?style=for-the-badge&logo=python&logoColor=white)](#)
[![Node.js](https://img.shields.io/badge/Node.js-services-339933?style=for-the-badge&logo=nodedotjs&logoColor=white)](#)

</div>

---

ITTH brings together AI-assisted campus utilities, creative agents, and presentation tools in one evolving project. Use it to work with slides, explore focused assistants, and run small tools for everyday study and productivity tasks.

> **Built to be useful, modular, and easy to grow.** Each tool can focus on a job while sharing the project's AI and service foundations.

## What’s inside

| Area | What it does |
| --- | --- |
| **Presentation Controller** | Controls PowerPoint presentations with a local web interface, slide navigation, annotations, thumbnails, voice features, and multiple-deck support. |
| **Campus Suite** | Desktop launcher for campus-focused AI features and productivity tools. |
| **AI agents** | Focused assistants for study, coding, quizzes, resumes, Blender workflows, and video editing. |
| **Python tools** | Standalone utilities for campus, classroom, and productivity workflows. |
| **Local services** | Node.js web and socket services connect the presentation interface to its Python controller. |

## Quick start

### Presentation Controller

**Requirements:** Python, Node.js/npm, and Microsoft PowerPoint for full slide control on Windows.

Install the root Node dependencies:

```powershell
npm install
```

Start the controller in local HTTP mode:

```powershell
$env:USE_HTTP = 'true'
$env:NODE_BIND_HOST = '127.0.0.1'
python .\pptcontroller.py
```

Use the app’s **Start Services** control, then open [http://127.0.0.1:8000](http://127.0.0.1:8000). The local API runs on port `9005`.

### Campus Suite desktop launcher

```powershell
python ".\campus suite.py"
```

The desktop launcher is separate from the Presentation Controller. It requires Node.js/npm and the Electron dependencies in `ai-agent-overlay\node_modules`.

## Configuration

- Start with [`.env.example`](.env.example) and put provider credentials in a local `.env` file.
- Keep API keys and private certificates out of source control.
- Optional provider integrations stay disabled or report as unconfigured when their credentials are missing.
- For local controller use, keep `NODE_BIND_HOST` set to `127.0.0.1` so the service remains on your computer.

See the [Competition Runbook](docs/COMPETITION_RUNBOOK.md) for service checks, ports, and recovery guidance.

## Project map

```text
.
├── ppt_controller/          # Python presentation controller
├── public/                  # Web interface and slide thumbnails
├── agents/                  # Modular assistant agents
├── teacher_tools/           # Classroom and teacher utilities
├── python_tools/            # Standalone Python tools
├── ai-agent-overlay/        # Electron desktop app components
├── blender_agent_workspace/ # Blender agent workspace
└── docs/                    # Runbooks and project notes
```

## A note on models

Large model weights are kept outside this repository so the source stays practical to clone. Add the model files locally when a workflow needs them.

## Contributing

ITTH is an evolving collection. Keep tools focused, document the setup they need, and use environment variables for credentials. For a new tool, include a short description of its purpose and a clear way to launch it.

---

<div align="center">

**ITTH** · Learn something. Build something. Make it work.

</div>
