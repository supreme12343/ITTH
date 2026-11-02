# Personal Growth Mentor

The Campus Suite student-and-teacher version of the Personal Growth Mentor. It helps students explore IT strengths, set small learning goals, and save progress check-ins. For teachers, it suggests supportive one-to-one prompts and right-sized challenges.

## Package contents

- `manifest.json` documents the agent metadata and available tools.
- `instructions.md` contains its role, boundaries, and response style.
- `agent.py` runs the model and bounded function-calling loop.
- `tools.py` defines the allow-listed goal tools and local persistence.

Goals are stored locally under `storage/agents/personal_growth_mentor/` in a file scoped to the agent conversation. The agent cannot access another conversation's saved goals through these tools.

Tool responses use a consistent `status`, `summary`, `next_actions`, and `artifacts` shape. Tool calls are capped per response and unknown names or invalid arguments return recovery guidance.
