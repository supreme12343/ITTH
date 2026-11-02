"""Run a prompt battery against the local Campus Suite GGUF model.

From the repository root: python test_local_model.py
Use --prompt to run a single custom prompt instead.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


DEFAULT_MODEL = Path(__file__).with_name("campus-suite-ai-Q4_K_M.gguf")
DEFAULT_SYSTEM_PROMPT = Path(__file__).with_name("campus-suite-ai-lora") / "system_prompt.txt"
_DLL_DIRECTORY_HANDLES = []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test the local Campus Suite GGUF model.")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL, help="Path to a .gguf file")
    parser.add_argument("--prompt", help="Run one custom prompt instead of the test battery")
    parser.add_argument("--system-prompt", type=Path, default=DEFAULT_SYSTEM_PROMPT)
    parser.add_argument("--max-tokens", type=int, default=80, help="Maximum response length")
    parser.add_argument(
        "--temperature", type=float, default=0.0,
        help="Sampling temperature (default: 0 for repeatable prompt checks)",
    )
    parser.add_argument("--top-p", type=float, default=1.0, help="Top-p sampling value")
    parser.add_argument("--context", type=int, default=4096, help="Context window size")
    parser.add_argument("--threads", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument(
        "--gpu-layers",
        type=int,
        default=8,
        help="Layers to offload to a supported GPU (default: 8 for the MX250)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Show llama.cpp model-loader diagnostics"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model_path = args.model.expanduser().resolve()

    if not model_path.is_file():
        print(f"Model file not found: {model_path}", file=sys.stderr)
        return 2
    if model_path.suffix.lower() != ".gguf":
        print("Expected a .gguf model file.", file=sys.stderr)
        return 2

    try:
        with model_path.open("rb") as model_file:
            magic = model_file.read(4)
    except OSError as exc:
        print(f"Could not read model file: {exc}", file=sys.stderr)
        return 2
    if magic != b"GGUF":
        print("The file does not have a valid GGUF header.", file=sys.stderr)
        return 2

    if not args.system_prompt.is_file():
        print(f"System prompt file not found: {args.system_prompt}", file=sys.stderr)
        return 2

    system_prompt = args.system_prompt.read_text(encoding="utf-8").strip()
    if not system_prompt:
        print(f"System prompt file is empty: {args.system_prompt}", file=sys.stderr)
        return 2

    try:
        # CUDA wheels use NVIDIA's pip-provided runtime DLLs on Windows.
        # Keep the DLL directory handles alive and add the folders to PATH
        # before importing llama_cpp so dependent DLLs can be resolved.
        nvidia_bin_dirs = [
            Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
            Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cuda_runtime" / "bin",
        ]
        for dll_dir in nvidia_bin_dirs:
            if dll_dir.is_dir():
                _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(dll_dir)))
                os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")

        from llama_cpp import Llama
        from llama_cpp import llama_cpp
    except ImportError:
        print(
            "Missing llama-cpp-python. Install the CPU runtime with:\n"
            "  python -m pip install llama-cpp-python "
            "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu",
            file=sys.stderr,
        )
        return 3

    if args.gpu_layers > 0 and not llama_cpp.llama_supports_gpu_offload():
        print(
            "This llama-cpp-python build has no GPU offload support. "
            "Install a CUDA-enabled wheel or pass --gpu-layers 0.",
            file=sys.stderr,
        )
        return 3

    print(f"CUDA GPU offload supported: {llama_cpp.llama_supports_gpu_offload()}", flush=True)
    print(
        f"Loading {model_path.name} ({model_path.stat().st_size / (1024 ** 3):.2f} GiB)...",
        flush=True,
    )
    try:
        model = Llama(
            model_path=str(model_path),
            n_ctx=args.context,
            n_threads=max(1, args.threads),
            n_gpu_layers=max(0, args.gpu_layers),
            verbose=args.verbose,
        )
    except Exception as exc:
        print(f"Model load or generation failed: {exc}", file=sys.stderr)
        if "Failed to load model from file" in str(exc):
            print(
                "Try again with --verbose. If the loader reports a missing "
                "'blk.*.attn_norm.weight' tensor, this GGUF's layer metadata "
                "expects weights that are absent; export or quantize it again "
                "from the complete merged checkpoint.",
                file=sys.stderr,
            )
        return 1

    print(f"System prompt: {args.system_prompt.resolve()}", flush=True)
    print("Loaded. Running prompt battery...\n", flush=True)

    def ask(messages: list[dict[str, str]], label: str) -> str:
        print(f"--- {label} ---", flush=True)
        result = model.create_chat_completion(
            messages=messages,
            max_tokens=max(1, args.max_tokens),
            temperature=max(0.0, args.temperature),
            top_p=min(1.0, max(0.0, args.top_p)),
            repeat_penalty=1.1,
        )
        answer = (result["choices"][0]["message"].get("content") or "").strip()
        usage = result.get("usage", {})
        print(answer or "(empty response)", flush=True)
        if usage:
            print(f"Tokens: {usage.get('prompt_tokens', '?')} prompt, "
                  f"{usage.get('completion_tokens', '?')} generated", flush=True)
        print(flush=True)
        return answer

    if args.prompt is not None:
        ask([{"role": "system", "content": system_prompt},
             {"role": "user", "content": args.prompt}], "Custom prompt")
        return 0

    prompts = [
        ("Identity - who are you?", "Who are you?"),
        ("Identity - who made you?", "Who made you?"),
        ("Identity - model", "What model are you?"),
        ("Capability", "What can you help me with?"),
        (
            "Project reasoning",
            "Campus Suite's Export button appears to do nothing, but "
            "export_report() works when called manually. Name two checks "
            "in the UI-to-backend path before changing the report function.",
        ),
    ]
    for label, prompt in prompts:
        ask([{"role": "system", "content": system_prompt},
             {"role": "user", "content": prompt}], label)

    history = [{"role": "system", "content": system_prompt}]
    history.append({"role": "user", "content": "My favourite number is 37."})
    first_memory_reply = ask(history, "Conversation memory - message 1")
    history.append({"role": "assistant", "content": first_memory_reply})
    history.append({"role": "user", "content": "What number did I tell you?"})
    ask(history, "Conversation memory - message 2")

    ask(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": "Was the underlying language model trained entirely "
          "from random initialization? If not, name its foundation checkpoint "
          "and briefly describe how Campus Suite AI was made."}],
        "System prompt - truthful provenance",
    )
    ask(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": "Have you run Campus Suite software tests in this "
          "conversation? Answer only from evidence in this conversation."}],
        "System prompt - do not invent test results",
    )
    ask(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": "What is known about how long the developers worked "
          "on this project and where the training examples came from? State only recorded "
          "facts; say when a detail is unknown."}],
        "System prompt - avoid unsupported details",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
