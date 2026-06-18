"""Analysis templates and AI system prompts.

Template prompts are written in English but instruct the model to answer in the
same language as the meeting transcript, so a French meeting yields a French
report, an English meeting an English one, and so on.
"""

# Each template: stable id -> localized label/desc (en/fr) + a language-neutral prompt.
TEMPLATES = {
    "minutes": {
        "label": {"en": "Minutes", "fr": "Compte rendu"},
        "desc": {"en": "Structured minutes: topics, key points, decisions, to-dos",
                 "fr": "Compte rendu structuré : sujets, points clés, décisions, actions"},
        "prompt": (
            "Produce structured meeting minutes based on the transcript, including:\n"
            "1. Meeting topic and participants (infer what you reasonably can; do not invent)\n"
            "2. Discussion points grouped by topic\n"
            "3. Decisions and conclusions reached\n"
            "4. Action items (owner, deadline; mark \"unspecified\" if not stated)\n"
            "Cite key quotes with their timestamps."
        ),
    },
    "actions": {
        "label": {"en": "Action items", "fr": "Actions"},
        "desc": {"en": "Action items only: task, owner, deadline, dependencies",
                 "fr": "Actions uniquement : tâche, responsable, échéance, dépendances"},
        "prompt": (
            "Extract action items only, as a table: Task | Owner | Deadline | Dependencies/Notes.\n"
            "If an owner or deadline is not explicitly stated in the transcript, mark it "
            "\"unspecified\"; do not guess.\n"
            "End with an \"Open / unowned\" section: things raised that no one took on."
        ),
    },
    "emotion": {
        "label": {"en": "Emotional arc", "fr": "Courbe émotionnelle"},
        "desc": {"en": "Mood and energy over time, with turning points",
                 "fr": "Humeur et énergie au fil du temps, avec points de bascule"},
        "prompt": (
            "Analyze how the mood and energy of this conversation evolve over time:\n"
            "1. Describe the emotional trajectory in time-based segments (opening -> stages -> close)\n"
            "2. Flag clear turning points (who said what that shifted the mood, with timestamps)\n"
            "3. Which topics visibly engaged or made the other side withdraw\n"
            "4. Overall read: what was the real temperature of this conversation"
        ),
    },
    "tension": {
        "label": {"en": "Tension & disagreement", "fr": "Tensions et désaccords"},
        "desc": {"en": "Unspoken disagreement, avoided topics, gaps in position",
                 "fr": "Désaccords tus, sujets évités, écarts de position"},
        "prompt": (
            "Surface the hidden tension in this conversation:\n"
            "1. Places where people agree on the surface but hedge their wording (quote + timestamp)\n"
            "2. Topics that were deflected or deliberately avoided\n"
            "3. The gap between each party's real position and their stated one\n"
            "4. If left unaddressed, where these tensions are likely to erupt next\n"
            "Stay disciplined: base everything on transcript evidence and rate your confidence per point."
        ),
    },
    "bias": {
        "label": {"en": "Cognitive bias", "fr": "Biais cognitifs"},
        "desc": {"en": "Detect cognitive biases in the discussion and decisions",
                 "fr": "Repérer les biais cognitifs dans la discussion et les décisions"},
        "prompt": (
            "Examine the decision quality in this discussion:\n"
            "1. Which cognitive biases appear (anchoring, confirmation, sunk cost, groupthink, "
            "etc.), with quotes + timestamps as evidence\n"
            "2. Which key assumptions went unchallenged\n"
            "3. Which counter-arguments that should have been raised were missing\n"
            "4. Give 2-3 questions worth revisiting"
        ),
    },
    "followup": {
        "label": {"en": "Threads to pull", "fr": "Pistes à creuser"},
        "desc": {"en": "Topics worth digging into, open questions, next research",
                 "fr": "Sujets à approfondir, questions ouvertes, prochaines recherches"},
        "prompt": (
            "Based on this conversation, list:\n"
            "1. Topics mentioned but not developed that are worth digging into (and why)\n"
            "2. Questions that were raised but never answered\n"
            "3. Information blind spots the conversation exposed\n"
            "4. Suggested next steps: what to research, who to talk to, what to verify"
        ),
    },
    "client": {
        "label": {"en": "Client follow-up", "fr": "Suivi client"},
        "desc": {"en": "Client debrief: signals, objections, relationship, next moves",
                 "fr": "Débrief client : signaux, objections, relation, prochaines actions"},
        "prompt": (
            "Debrief this client conversation as a senior account director would:\n"
            "1. Buying / partnership signals and negative signals (quote + timestamp)\n"
            "2. Objections raised and the client's real concerns (said vs. unsaid)\n"
            "3. A read on the relationship temperature and the evidence for it\n"
            "4. A follow-up plan: what to do within 48 hours and within two weeks, plus email points"
        ),
    },
    "hiring": {
        "label": {"en": "Interview debrief", "fr": "Débrief d'entretien"},
        "desc": {"en": "Interview debrief: evidence, strengths, risks, follow-up questions",
                 "fr": "Débrief d'entretien : preuves, forces, risques, relances"},
        "prompt": (
            "Debrief this interview as an interviewer-coach would:\n"
            "1. Evidence of the candidate's abilities (concrete examples, not self-claims)\n"
            "2. Strengths and risk signals (quote + timestamp)\n"
            "3. Answers that were vague, contradictory, or embellished\n"
            "4. 3-5 questions worth probing in the next round"
        ),
    },
}


def template_label(tid: str, lang: str = "en") -> str:
    t = TEMPLATES.get(tid)
    if not t:
        return tid
    return t["label"].get(lang) or t["label"]["en"]


BRIEF_PROMPT = (
    "You are my daily briefing assistant. Below are all of today's meeting/recording "
    "transcripts (or existing reports). Produce a daily brief with:\n"
    "1. Today at a glance: how many meetings, one line of substance for each\n"
    "2. All action items consolidated (ordered by urgency)\n"
    "3. Strategic insight: read together, what does today's information tell me\n"
    "4. One reflection: where I could have communicated better today\n"
    "Be concise and self-archivable. Answer in the same language as the meeting content."
)

LONGTERM_PROMPT = (
    "You maintain a long-term memory file that spans many meetings. Below is the full "
    "current long-term memory and one new source (a meeting transcript, or a daily brief "
    "summarizing one day's meetings).\n"
    "Output the updated long-term memory in full (Markdown). Rules:\n"
    "1. Four fixed sections: ## People, ## Projects & Clients, ## Commitments & Decisions, "
    "## Terms & Phrasing\n"
    "2. Extract durable facts from the new source into the right section; merge and update "
    "duplicates rather than listing them twice\n"
    "3. Each entry under Commitments & Decisions notes: who, what was committed/decided, "
    "the date (if any), the status (in progress / fulfilled / cancelled / unknown), and the "
    "source [meeting id]\n"
    "4. When new meetings update a person/project (e.g. a changed position), keep the trail: "
    "\"said X [old meeting] -> now says Y [new meeting]\"\n"
    "5. Only record facts grounded in the source; do not speculate; drop small talk and "
    "one-off details\n"
    "6. Keep the whole file under ~600 lines; compress stale, low-value entries but never lose "
    "an unfulfilled commitment\n"
    "Output the full updated file, starting with \"# Long-term memory\", with no explanation. "
    "Keep section headings in English; keep individual entries in their original language."
)

TRACK_PROMPT = (
    "You are a cross-meeting tracking analyst. Below are several meeting transcripts in "
    "chronological order. Output three parts:\n"
    "1. Commitments and follow-through: who committed/agreed to what, and in which meeting "
    "(note [meeting id] and timestamp); in later meetings, was it fulfilled, progressing, or "
    "dropped\n"
    "2. Shifts in position: where the same person or party said different things about the "
    "same matter over time; quote both passages and judge what the change means\n"
    "3. Recurring open questions: matters raised repeatedly but never resolved, and how each "
    "time they were set aside\n"
    "Base everything on transcript evidence; do not invent. Where there is only a single-meeting "
    "thread with no basis for cross-meeting comparison, say so. Answer in the same language as "
    "the meeting content."
)

CHAT_SYSTEM = (
    "You are coco, my local meeting analysis assistant. Answer questions based on the meeting "
    "transcripts and global memory provided.\n"
    "Principles: rely only on transcript evidence and never fabricate; cite key quotes with "
    "timestamps; when something is not covered, say \"the transcript doesn't mention this\"; "
    "lead with the conclusion, then the evidence. Answer in the same language as the question."
)
