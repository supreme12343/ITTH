import json
import os
import re
from pathlib import Path
from typing import Optional

try:
    from markitdown import MarkItDown
    _MARKITDOWN_AVAILABLE = True
except ImportError:
    _MARKITDOWN_AVAILABLE = False

from key_manager import AiApi, MockRoleKeyManager


class QuizGenerator:
    def __init__(self, key_manager):
        self.api = key_manager if isinstance(key_manager, AiApi) else AiApi(
            use_mock=isinstance(key_manager, MockRoleKeyManager), key_manager=key_manager
        )

    def _extract_text(self, file_path: str) -> str:
        """Extract text from PDF, DOCX, or PPTX using markitdown."""
        if _MARKITDOWN_AVAILABLE:
            try:
                mid = MarkItDown()
                result = mid.convert(file_path)
                return result.text_content or ""
            except Exception:
                pass
        # Fallback: try to read as plain text
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except Exception:
            return ""

    def _build_prompt(self, source_text: str, topic: str, difficulty: str, count: int, q_types: list) -> str:
        type_str = ", ".join(q_types)
        topic_part = f" on the topic: {topic}" if topic else ""
        return f"""Generate a quiz with {count} questions{topic_part} at {difficulty} difficulty.
Question types to include: {type_str}.

Source material:
{source_text[:15000]}

Format the output as follows:
=== QUIZ ===

1. [Question Type] Question text?
   A) Option A
   B) Option B
   C) Option C
   D) Option D
   Answer: A
   Explanation: Brief explanation

2. [Question Type] Question text?
   A) Option A
   B) Option B
   C) Option C
   D) Option D
   Answer: B
   Explanation: Brief explanation

...

For True/False questions, use:
   A) True
   B) False

For Short Answer questions, omit options and provide:
   Answer: Expected answer
   Explanation: Brief explanation

Make questions clear, accurate, and appropriate for the difficulty level."""

    def generate(self, file_path: Optional[str], topic: str, difficulty: str, count: int, q_types: list, source_text: str = "") -> str:
        if file_path:
            source_text = self._extract_text(file_path)
            if not source_text.strip():
                return "⚠️ Could not extract text from the uploaded file."

        if not source_text.strip() and not topic.strip():
            return "⚠️ No source material provided. Upload a file or enter a topic."

        # Use topic as context if no file text
        if not source_text.strip():
            source_text = f"Topic: {topic}"

        prompt = self._build_prompt(source_text, topic, difficulty, count, q_types)

        if isinstance(self.api._km, MockRoleKeyManager):
            return self._mock_generate(prompt, count, q_types, difficulty)

        try:
            response = self.api.chat_with_role(
                "analyst",
                "Generate accurate, clearly formatted learning assessments. Follow the requested question types and exact count.",
                prompt,
                temperature=0.7,
                max_retries=1,
            )
            return response.choices[0].message.content or "⚠️ Empty response from model"
        except Exception as e:
            return f"⚠️ Error generating quiz: {e}"

    def _mock_generate(self, prompt: str, count: int, q_types: list, difficulty: str) -> str:
        """Generate a mock quiz for demo mode."""
        import random
        lines = ["=== QUIZ ===\n"]
        for i in range(1, count + 1):
            q_type = random.choice(q_types)
            if q_type == "Multiple Choice":
                lines.append(f"{i}. [{q_type}] Sample question {i} about the topic?")
                lines.append("   A) Option A")
                lines.append("   B) Option B")
                lines.append("   C) Option C")
                lines.append("   D) Option D")
                lines.append(f"   Answer: {random.choice(['A', 'B', 'C', 'D'])}")
                lines.append("   Explanation: This is a mock explanation for the answer.")
            elif q_type == "True/False":
                lines.append(f"{i}. [{q_type}] Sample statement {i} about the topic.")
                lines.append("   A) True")
                lines.append("   B) False")
                lines.append(f"   Answer: {random.choice(['A', 'B'])}")
                lines.append("   Explanation: This is a mock explanation for the answer.")
            else:  # Short Answer
                lines.append(f"{i}. [{q_type}] Sample question {i} about the topic?")
                lines.append("   Answer: Sample answer for this question.")
                lines.append("   Explanation: This is a mock explanation for the answer.")
            lines.append("")
        return "\n".join(lines)
