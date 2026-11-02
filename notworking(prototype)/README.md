# Video editing agent prototype

The current After Effects agent implementation is stored here while it is being
reworked for the competition. The project root `video_editing_agent.py` is a
compatibility loader; existing imports such as `from video_editing_agent import
VideoEditingAgent` continue to work.

## Current improvement

`VideoEditingAgent.start_server()` returns `True` when the file bridge is
running and `False` if its worker thread cannot start. The desktop prototype
uses that result to show the right bridge status.

## Next improvements to prioritize

- Add a dry run and undo flow before executing generated JSX.
- Show command progress and After Effects acknowledgements in the UI.
- Add focused checks for command generation, bridge timeouts, and malformed AE
  responses.
- Improve editing quality with reusable style presets and a final visual
  verification step.
