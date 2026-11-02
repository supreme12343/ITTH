"""Optional AI drafts and clearly labeled offline scaffolds for teacher tools."""


def ai_draft(tool, values):
    """Create one teacher draft through the shared Campus Suite AI boundary."""
    from campus_ai import ask_campus_ai

    prompts = {
        "lesson_planner": lambda: (
            "You are an experienced classroom lesson designer. Create a practical, editable lesson plan with timings, teacher actions, student actions, checks for understanding, and a closing assessment. Use the supplied objectives and constraints. Avoid inventing curriculum standards.",
            f"Subject/topic: {values['subject']}\nGrade: {values['grade']}\nDuration: {values['duration']} minutes\nObjectives:\n{values['objectives']}\nMaterials/constraints:\n{values['materials']}",
        ),
        "worksheet_builder": lambda: (
            "Create an original classroom practice worksheet, not a multiple-choice quiz. Include clear student directions, varied practice, one application task, and a brief exit ticket. Match the learner age and objective. Do not claim alignment to an unspecified curriculum.",
            f"Subject/topic: {values['subject']}\nGrade: {values['grade']}\nObjective: {values['focus']}\nLength: {values['length']}\nInclude teacher answer guide: {values['include_key']}",
        ),
        "rubric_builder": lambda: (
            "Draft a fair, specific analytic rubric. Give observable descriptions at every performance level, align criteria to the task, and make point totals add to 100. Avoid vague traits such as effort or personality. Clearly label this as a teacher-editable draft.",
            f"Assignment: {values['assignment']}\nGrade: {values['grade']}\nCriteria requested:\n{values['criteria']}\nScale: {values['scale']}",
        ),
        "differentiation_planner": lambda: (
            "Offer practical, respectful ways to adapt the activity for learner access. Organize by universal supports, scaffold options, language/access supports, and an extension. Keep learning goals ambitious and avoid assumptions or diagnoses. Do not request or include student names.",
            f"Subject/grade: {values['subject']}\nActivity:\n{values['activity']}\nPlanning needs:\n{values['needs']}",
        ),
    }
    try:
        system, prompt = prompts[tool]()
    except KeyError as exc:
        raise ValueError(f"No AI draft is configured for {tool!r}.") from exc
    return ask_campus_ai(system, prompt, role="analyst", temperature=0.35)


def offline_ai_draft(tool, values):
    """Return an editable local scaffold when Campus Suite AI is unavailable."""
    header = "OFFLINE TEMPLATE · Campus Suite AI is unavailable\nReview and adapt before classroom use.\n\n"

    if tool == "lesson_planner":
        return header + (
            f"LESSON PLAN · {values.get('subject', 'Subject / topic')} · Grade {values.get('grade', '—')}\n"
            f"Length: {values.get('duration', '—')} minutes\n\n"
            f"Learning objectives\n{values.get('objectives', '').strip() or '• Add the learning objectives.'}\n\n"
            f"Materials and constraints\n{values.get('materials', '').strip() or '• Add available materials and constraints.'}\n\n"
            "1. Launch · about 10%\nConnect to prior learning and share the goal.\n\n"
            "2. Model and explore · about 25%\nDemonstrate the target skill; ask learners to explain what they notice.\n\n"
            "3. Guided practice · about 35%\nWork through an example together and check understanding before moving on.\n\n"
            "4. Independent application · about 20%\nLearners apply the skill to a new example while the teacher checks in.\n\n"
            "5. Exit check · about 10%\nUse one short response or demonstration to decide what to revisit next."
        )

    if tool == "worksheet_builder":
        subject = values.get("subject", "the topic")
        grade = values.get("grade", "the class")
        focus = values.get("focus", "the target skill")
        output = (
            header + f"WORKSHEET SCAFFOLD · {subject} · Grade {grade}\nSkill: {focus}\n"
            f"Length: {values.get('length', 'teacher choice')}\n\n"
            "Directions: Show your thinking. Use the lesson notes or examples when helpful.\n\n"
            f"1. Explain {focus} in your own words.\n\n"
            f"2. Practice {focus}: write or solve one example, then show the steps you used.\n\n"
            f"3. Apply {focus} to a new situation. What changed, and what stayed the same?\n\n"
            "4. Check your work: identify one part you are confident about and one question you still have.\n\n"
            "EXIT TICKET\nWhat is one idea from today you can explain without notes?"
        )
        if values.get("include_key", True):
            output += "\n\nTEACHER GUIDE\nAccept responses that correctly use the target skill and show reasoning; add topic-specific sample answers before sharing."
        return output

    if tool == "rubric_builder":
        criteria_text = values.get("criteria", "")
        criteria = [item.strip(" •\t") for item in criteria_text.replace(",", "\n").splitlines() if item.strip(" •\t")]
        if not criteria:
            criteria = ["Accuracy", "Reasoning", "Communication"]
        scale = str(values.get("scale", "4 levels"))
        if scale.startswith("3"):
            levels = [(3, "Extending"), (2, "Meeting"), (1, "Developing")]
        elif scale.startswith("5"):
            levels = [(5, "Advanced"), (4, "Strong"), (3, "Developing"), (2, "Emerging"), (1, "Beginning")]
        else:
            levels = [(4, "Exceeds"), (3, "Meets"), (2, "Developing"), (1, "Beginning")]
        base, remainder = divmod(100, len(criteria))
        rows = []
        for index, criterion in enumerate(criteria):
            points = base + (1 if index < remainder else 0)
            descriptions = []
            for level, label in levels:
                score = round(points * level / levels[0][0])
                descriptor = {
                    1: "needs substantial revision or support",
                    2: "shows partial success with gaps to address",
                    3: "meets the stated expectations with clear evidence",
                    4: "shows accurate, independent, and well-supported work",
                    5: "extends the learning with precise and insightful work",
                }[level]
                descriptions.append(f"{label} ({score}/{points} pts): {descriptor} for {criterion.lower()}")
            rows.append(f"{criterion} · {points} points maximum\n" + "\n".join(descriptions))
        return header + (
            f"RUBRIC SCAFFOLD · {values.get('assignment', 'Assignment')} · Grade {values.get('grade', '—')}\n"
            "Adjust the descriptors to match the assignment evidence. Criterion maxima total 100 points.\n\n"
            + "\n\n".join(rows)
        )

    if tool == "differentiation_planner":
        return header + (
            f"SUPPORT PLAN · {values.get('subject', 'Subject / grade')}\n"
            f"Activity: {values.get('activity', 'Add the activity')}\n"
            f"Planning needs: {values.get('needs', '').strip() or 'Choose access needs based on classroom evidence.'}\n\n"
            "Universal supports\n• State the goal and success criteria; model one example; provide a visual sequence.\n\n"
            "Scaffolds\n• Break the task into smaller checkpoints and offer a worked example or sentence starter.\n\n"
            "Language and access\n• Preview key vocabulary, allow more than one way to respond, and make directions available in short written steps.\n\n"
            "Extension\n• Ask learners who are ready to justify a choice, compare strategies, or apply the same goal to a less familiar example.\n\n"
            "Check whether each support improves access while keeping the learning goal consistent."
        )

    raise ValueError(f"No offline draft template is configured for {tool!r}.")


def draft_with_fallback(tool, values):
    """Try the shared AI first and return a labeled local draft on failure."""
    try:
        return ai_draft(tool, values), False
    except Exception:
        return offline_ai_draft(tool, values), True
