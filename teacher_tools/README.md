# Campus Suite teacher tools

Ten standalone CustomTkinter teacher apps using Campus Suite's shared theme:

1. Lesson plan builder — editable AI draft from objectives and constraints.
2. Worksheet & exit ticket — original practice sheet with an optional answer guide.
3. Rubric builder — criteria-based, 100-point analytic rubric draft.
4. Differentiation planner — access supports, scaffolds, and extensions for an activity.
5. Seating chart maker — randomized or alphabetical seat layout.
6. Balanced group maker — distribute optional skill levels across groups.
7. Station rotation planner — time a sequence of classroom stations.
8. Family conference notes — strengths-based agenda and follow-up record template.
9. Participation log — local CSV records and a simple class summary.
10. Assignment tracker — local CSV list ordered by due date.
11. Learning Arcade — five local educational games for fractions, ecosystems, media literacy, code tracing, and historical sequencing, with optional AI hints.
12. Exit-Ticket Analyzer — group responses by question, find common incorrect answers, and export reteaching notes while ignoring extra identity columns.

Open these from Campus Suite's **Teacher tools** filter or run any `*.py` app directly. The four planning drafts use the existing `key_manager` through `AiApi`; they require configured provider credentials. All teacher tool windows include optional AI follow-ups or hints. They ask for confirmation before sending anything; review and remove student names or other private details first. Participation and assignment records stay in local CSV files under `storage/`.
