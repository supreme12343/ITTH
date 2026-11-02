"""
Standalone Video Editing Agent Runner
Run this directly to start the HTTP server and test the connection.
"""

import time
import sys
import os

# Add the project directory to path
project_dir = os.path.dirname(os.path.abspath(__file__))
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

from video_editing_agent import VideoEditingAgent
from key_manager import find_ae_scriptui_panels_dir, AiApi


def main():
    print("=" * 60)
    print("Video Editing Agent - Standalone Runner")
    print("=" * 60)
    
    # Initialize the AI API (connects to OmniRoute or Gemini)
    api = AiApi()
    print(f"[AI] Connected: {api.get_status()['mode']}")
    
    agent = VideoEditingAgent(key_manager=api._km)
    agent.set_log_callback(lambda m: print(m))
    
    print("\nStarting Bridge Server...")
    agent.start_server()
    
    # Wait for server to start
    time.sleep(1)
    
    # Find AE ScriptUI Panels directory automatically
    ae_panels_dir = find_ae_scriptui_panels_dir()
    
    if ae_panels_dir and os.path.exists(ae_panels_dir):
        script_path = os.path.join(ae_panels_dir, "AI_Live_Receiver.jsx")
        try:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(agent.get_ae_panel_script())
            print(f"\n[OK] AE Panel script auto-saved to: {script_path}")
            print("  -> Restart After Effects to auto-load, or run Window > AI_Live_Receiver.jsx")
        except Exception as e:
            print(f"\n[ERROR] Failed to save AE panel script: {e}")
    else:
        print(f"\n[WARN] AE Panels folder not found automatically.")
        print("Please save the script manually to your After Effects Scripts/ScriptUI Panels folder:")
        print("--------------------------------------------------")
        print(agent.get_ae_panel_script()[:300] + "\n... (script truncated)")
        print("--------------------------------------------------")
    
    print("\n" + "=" * 60)
    print("AI Agent Bridge is LIVE. Commands:")
    print("  - Type a prompt and press Enter to send to AE")
    print("  - Example: 'add text Hello World'")
    print("  - Example: 'glow effect'")
    print("  - Example: 'color grade'")
    print("  - Example: 'render'")
    print("  - Type 'quit' to exit")
    print("=" * 60 + "\n")
    
    try:
        while True:
            prompt = input("Enter prompt > ").strip()
            if prompt.lower() in ('quit', 'exit', 'q'):
                break
            if prompt:
                cmd_id = agent.send_prompt(prompt)
                if cmd_id:
                    print(f"  -> Queued for AE execution (ID: {cmd_id})")
                else:
                    print(f"  -> Empty prompt ignored")
    except KeyboardInterrupt:
        pass
    
    print("\nStopping Agent Bridge...")
    agent.stop_server()
    print("Done.")


if __name__ == "__main__":
    main()