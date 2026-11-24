from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  
    load_dotenv = None

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data"
OUT.mkdir(exist_ok=True)

if load_dotenv:
    load_dotenv(ROOT / ".env")

ELEVEN_API_KEY = os.environ.get("ELEVEN_LABS_API_KEY", "")
ELEVEN_VOICE_DEV_A = os.environ.get("ELEVEN_VOICE_DEV_A", "")
ELEVEN_VOICE_DEV_B = os.environ.get("ELEVEN_VOICE_DEV_B", "")

DEFAULT_VOICES = {
    "dev_a": "21m00Tcm4TlvDq8ikWAM",  
    "dev_b": "pNInz6obpgDQGcFmaJgB",  
}

def resolve_voice_id(voice_name_or_id: str, fallback_id: str) -> str:
    if voice_name_or_id and len(voice_name_or_id) > 15 and " " not in voice_name_or_id:
        return voice_name_or_id
    if ELEVEN_API_KEY and voice_name_or_id:
        try:
            from elevenlabs import ElevenLabs
            client = ElevenLabs(api_key=ELEVEN_API_KEY)
            voices = client.voices.get_all()
            for v in voices.voices:
                if v.name.lower() == voice_name_or_id.lower():
                    return v.voice_id
        except Exception:
            pass
    return fallback_id

def synthesize_elevenlabs(text: str, voice: str, out_path: Path) -> None:
    from elevenlabs import ElevenLabs, save
    client = ElevenLabs(api_key=ELEVEN_API_KEY)
    audio = client.text_to_speech.convert(
        voice_id=voice,
        output_format="mp3_44100_128",
        text=text,
    )
    save(audio, str(out_path))

def synthesize_say(text: str, voice: str, out_path: Path) -> None:
    subprocess.run(
        ["say", "-v", voice, "-r", "178", "-o", str(out_path), text],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

def duration(path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], text=True)
    return float(out.strip())

def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def main():
    parts = OUT / "_parts"
    parts.mkdir(exist_ok=True)
    silence = parts / "gap.wav"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "0.45", "-c:a", "pcm_s16le", str(silence)])

    dev_a_voice = ELEVEN_VOICE_DEV_A or DEFAULT_VOICES["dev_a"]
    dev_b_voice = ELEVEN_VOICE_DEV_B or DEFAULT_VOICES["dev_b"]
    if ELEVEN_API_KEY:
        dev_a_voice = resolve_voice_id(ELEVEN_VOICE_DEV_A, DEFAULT_VOICES["dev_a"])
        dev_b_voice = resolve_voice_id(ELEVEN_VOICE_DEV_B, DEFAULT_VOICES["dev_b"])
        print(f"Developer A voice ID: {dev_a_voice}")
        print(f"Developer B voice ID: {dev_b_voice}")

    use_elevenlabs = bool(ELEVEN_API_KEY)
    if use_elevenlabs:
        print("Using ElevenLabs for voice generation")
    else:
        print("No ELEVEN_LABS_API_KEY found — falling back to macOS `say`")

    LINES = [
        ("Developer A", "Samantha", "Hi, thanks for joining the meeting. Today, I want to walk you through the EarnOut backend so you can understand how the complete system works, what we have implemented so far, and what still needs attention."),
        ("Developer B", "Daniel", "Sounds good. Before we get into the technical implementation, could you give me a brief overview of what EarnOut actually does and what the backend is responsible for?"),
        ("Developer A", "Samantha", "Absolutely. EarnOut is essentially an AI-powered business-planning application. The main idea is that a founder provides information about their business problem, its context, and its impact. Based on that information, our backend uses AI to generate a complete business-planning structure."),
        ("Developer A", "Samantha", "This includes MVP, mission, vision and purpose generation. Potential solutions and organisational culture. Customer personas and market analysis. TAM, SAM and SOM calculations. Operating processes and business capabilities. O-NET occupation matching and Functional Job Analysis. Mental action sequences and Unity simulation scenes. Customer Value Proposition and business strategy. KPI generation and OKR cascades. Employee seats, weekly calendars and execution tracking."),
        ("Developer A", "Samantha", "Apart from the main business-planning workflow, we also have meetings, an AI meeting assistant called Otto, document knowledge management, chat, pitch-deck analysis, X-B-R-L reporting and management dashboards."),
        ("Developer B", "Daniel", "That's quite an extensive system. So, is everything handled through one backend application, or are these separate services?"),
        ("Developer A", "Samantha", "The backend is primarily a single Python FastAPI application. We have separated its functionality into different modules and sub-applications to keep the code organised."),
        ("Developer B", "Daniel", "Could you explain the technology stack and how the different components communicate with each other?"),
        ("Developer A", "Samantha", "Sure. Our main technologies are Python with FastAPI for backend development. Neo-four-jay Aura as our graph database and primary data store. Open-AI models integrated through Lang-Chain. Docker for containerisation and deployment. AWS services, particularly S3, for file storage. Send-Grid for email communication. Stream for meeting video and chat functionality. O-NET and external X-B-R-L APIs for external data integrations."),
        ("Developer A", "Samantha", "The main application is located in rag slash main dot py. We have separate modules such as rag slash utils dot py for database fetching, storing and helper functions. rag slash prompts dot py for most AI prompt templates. rag slash modular pipeline dot py for the staged mental-action generation pipeline."),
        ("Developer A", "Samantha", "rag slash shared dot py for LLM clients, model configuration, caching and Neo4j connectivity. rag slash tools for deterministic calculations, FJA scoring, calendar generation and Unity scene assembly. rag slash auth dot py for authentication. rag slash meeting dot py for meetings and invitations."),
        ("Developer A", "Samantha", "rag slash okr dot py for OKR spreadsheet operations. rag slash management dot py for management dashboards. rag slash pitch deck dot py for pitch-deck analysis. rag slash chat dot py, rag slash x-b-r-l dot py and rag slash n-l-p dot py for their respective functionalities."),
        ("Developer B", "Daniel", "You mentioned Neo4j is the primary database. Are we using any additional database for user information or application data?"),
        ("Developer A", "Samantha", "No. Neo4j Aura is the only primary data store. We use a graph-based structure where most data is connected through the User node or carries a user-id property."),
        ("Developer A", "Samantha", "This allows us to maintain relationships between business information, departments, activities, employees, objectives and other entities. We also use Neo4j vector indexes for retrieval-augmented generation, particularly for the chat and document-related functionality."),
        ("Developer B", "Daniel", "Understood. So, the database structure is an important part of the entire application, not just a place to store records."),
        ("Developer A", "Samantha", "Exactly. The relationships are essential because many downstream modules depend on previously generated information being available in Neo4j."),
        ("Developer B", "Daniel", "Can you walk me through the actual onboarding pipeline? I'd like to understand how the information moves from the initial user input to the final business plan."),
        ("Developer A", "Samantha", "Of course. The onboarding process is a sequence of AI-powered generation steps. The first step is MVP generation. The founder provides their Problem, Context and Impact, which we call P-C-I."),
        ("Developer A", "Samantha", "The backend processes this information and generates the initial MVP structure. After that, we move through the following stages. First, Solution and Culture Generation."),
        ("Developer A", "Samantha", "We generate potential business solutions, allow the user to select one, and then generate the organisational culture and rituals. Second, Customer and Market Analysis."),
        ("Developer A", "Samantha", "We generate customer personas, operating processes and capabilities. Then we generate customer entry points, advertising messages, promotional strategies and competitor analysis. After that, the system calculates T-A-M, S-A-M and S-O-M."),
        ("Developer A", "Samantha", "Third, Business Process and Workforce Analysis. We identify key business steps and map them to O-NET occupations. We then perform Functional Job Analysis, or F-J-A, to understand the requirements of different activities."),
        ("Developer A", "Samantha", "Fourth, Mental Action and Unity. We generate mental-action sequences through a modular pipeline. This pipeline has multiple stages: Stage A, Initial planning and imaginal buffer. Stage B zero and Stage B, Klein frames."),
        ("Developer A", "Samantha", "Stage C, Execution and identity. Stage D, F-J-A scoring and cost-related information. Once the mental-action information is generated, a deterministic Python Unity compiler converts it into scene JSON."),
        ("Developer A", "Samantha", "Fifth, Strategy and Business Execution. We generate the Customer Value Proposition, strategy, ideal outcomes, KPIs and navigation strategy. Finally, we generate the OKR cascade from company-level objectives down to departments, teams and individual seats."),
        ("Developer A", "Samantha", "The system then generates weekly calendars for those seats."),
        ("Developer B", "Daniel", "So, each stage depends on the previous stage?"),
        ("Developer A", "Samantha", "Correct. This is one of the most important things to understand about the backend. The routes don't directly pass their generated data to each other. Instead, each stage reads the previously stored information from Neo4j."),
        ("Developer A", "Samantha", "For example, if persona generation hasn't completed successfully, the operating-process generation may not have the information it needs. Therefore, when debugging an empty screen or missing output, we should first verify whether the earlier generation steps completed and stored their results correctly."),
        ("Developer B", "Daniel", "That makes sense. We should check the database and pipeline execution order before assuming that the AI prompt itself is faulty."),
        ("Developer A", "Samantha", "Exactly."),
        ("Developer B", "Daniel", "Let's discuss the AI integration. Which models are we currently using, and how are the prompts managed?"),
        ("Developer A", "Samantha", "The backend primarily uses OpenAI's pinned model: gpt-five point four-two thousand twenty-six dash zero three dash zero-five. Most of the LLM clients use temperature zero to maintain consistent and predictable outputs."),
        ("Developer A", "Samantha", "We use LangChain to manage the prompt templates and model execution. The main configuration is in rag slash shared dot py. We have multiple clients, including llm for standard JSON responses, llm one for large outputs, llm two for large streaming responses."),
        ("Developer A", "Samantha", "llm three for medium-sized streaming responses, llm tool for tool-calling operations, llm tool i-d for tool identification. There is also a newer llm voice client specifically for Otto's spoken responses."),
        ("Developer A", "Samantha", "It uses temperature zero point seven, with a four-thousand-token output limit and a forty-five-second timeout."),
        ("Developer B", "Daniel", "Why do we have so many different clients if they are all using the same OpenAI model?"),
        ("Developer A", "Samantha", "Because different operations have different requirements. Some prompts generate small JSON responses, while others generate very large outputs."),
        ("Developer A", "Samantha", "For example, the mental-action pipeline can produce extremely large responses. If we use the standard client, we may hit output-token limits or gateway timeouts. That's why we use the streaming client, particularly llm two, for larger generation stages."),
        ("Developer B", "Daniel", "How are the prompts connected to the API routes?"),
        ("Developer A", "Samantha", "We use something called a chain ID. When a route needs an AI response, it prepares the required input, serialises it into JSON and passes it to cached underscore llm underscore invoke along with the chain ID."),
        ("Developer A", "Samantha", "The shared module identifies the appropriate prompt and model client. For example, the MVP generation route calls the MVP generation prompt through the corresponding chain ID. This keeps prompt definitions separate from the route implementation."),
        ("Developer B", "Daniel", "And how many prompts are currently documented?"),
        ("Developer A", "Samantha", "The original prompt catalogue documented seventy-five entries: fifty-three production prompts, twelve testing prompts and ten unused prompts."),
        ("Developer A", "Samantha", "The latest update added nineteen prompt templates and two additional system texts. Some existing prompts were also modified. No existing prompt names were removed or renamed."),
        ("Developer A", "Samantha", "The prompt catalogue is organised into five main categories: Onboarding, Persona and Market. Business Process, R-O-P and F-J-A. Mental Action and Unity. Value Proposition, Strategy and K-P-Is. OKR, Otto and Other AI Calls."),
        ("Developer B", "Daniel", "If we need to modify an existing prompt, how should we test it?"),
        ("Developer A", "Samantha", "We should first identify the route and chain ID, then locate the relevant prompt. After making a change, we should verify the input data, check the output structure and compare the result with previously saved outputs in rag slash output."),
        ("Developer A", "Samantha", "We should also remember that the LLM cache may return an old response even after changing the prompt. Using force underscore refresh or restarting the server can help, but we need to be careful because the current cache-clearing logic clears the entire process-wide cache, not just one user's entry."),
        ("Developer B", "Daniel", "Apart from the onboarding pipeline, which other modules should I understand before taking ownership?"),
        ("Developer A", "Samantha", "There are several important modules. First, Authentication and User Management. The authentication module supports email O-T-P signup, login, refresh tokens, logout, password reset, OAuth login, team members and account deletion."),
        ("Developer A", "Samantha", "Access tokens last approximately sixty days, while refresh tokens last approximately three hundred sixty-five days."),
        ("Developer A", "Samantha", "Second, Meetings and Calendar. The meeting module handles team invitations, meeting creation, joining meetings, calendar entries and Stream video slash chat tokens. Daily meeting emails are handled by A-P-Scheduler."),
        ("Developer A", "Samantha", "Third, Otto – The AI Meeting Assistant. Otto is an important part of the system. It reads meeting transcripts, identifies commitments and questions, checks participants' seat calendars and proposes tasks."),
        ("Developer A", "Samantha", "The placement process has two important steps: POST slash otto slash propose-placement finds an available calendar slot and generates a signed placement token. POST slash otto slash confirm-placement verifies the token and then saves the task to the calendar."),
        ("Developer A", "Samantha", "Otto also has session memory to avoid duplicating proposals across multiple calls. In the latest update, its functionality has expanded to include spoken responses, private chat, company-related questions and knowledge-directory integration."),
        ("Developer A", "Samantha", "Fourth, OKR and Management. The OKR module supports spreadsheet uploads, classification, comparison, attachment and importing existing work trackers into the OKR structure."),
        ("Developer A", "Samantha", "The management module provides a dashboard containing business-cycle information, department objectives, activities and workload information. Fifth, X-B-R-L Reporting."),
        ("Developer A", "Samantha", "The X-B-R-L accounting functionality generates financial reports for an OKR activity. An important design decision here is that AI identifies what was used and the supporting evidence, while deterministic Python code performs the actual financial calculations."),
        ("Developer A", "Samantha", "The generated accounting report is saved and reused rather than regenerated every time. Sixth, Knowledge Directory."),
        ("Developer A", "Samantha", "This is one of the newer additions. Documents are stored in AWS S3, while their extracted text and associated information are stored in Neo4j."),
        ("Developer A", "Samantha", "The system can extract text and pictures, propose document tags using AI, index the content and allow Otto to search relevant documents."),
        ("Developer B", "Daniel", "That's helpful. So, the Knowledge Directory is also connected to Otto, rather than being an isolated document-upload feature?"),
        ("Developer A", "Samantha", "Correct. The idea is to make relevant company knowledge available to the assistant while respecting the application's access permissions."),
        ("Developer B", "Daniel", "Now that I understand the architecture, I'd like to know which parts are fully completed and which ones still require development."),
        ("Developer A", "Samantha", "A significant amount of functionality has already been implemented. The main onboarding pipeline, O-NET integration, F-J-A phases, mental-action generation, Unity scene assembler, C-V-P, strategy, KPI generation, OKR cascade and seat calendar functionality are implemented."),
        ("Developer A", "Samantha", "Otto's task extraction and calendar placement, meeting management, pitch-deck analysis, RAG chat, management dashboard and OKR spreadsheet functionality are also present."),
        ("Developer A", "Samantha", "However, we still have some important issues. Let me highlight the main ones. First, F-J-A D-two and Mental Action Limits."),
        ("Developer A", "Samantha", "The F-J-A D-two route and mental-action generation route still contain test limitations that process only the first input. This means the complete set of activities is not processed."),
        ("Developer A", "Samantha", "Second, Cognitive Thinking Storage. The cognitive-thinking route generates scenes, but its database storage call remains commented out. The API can report success even though the scenes are not actually persisted."),
        ("Developer A", "Samantha", "Third, Missing Database Relationships. Some routes expect relationships that are never created. For example, HAS-C-V-P, HAS-OBJECTIVE and HAS-TURN."),
        ("Developer A", "Samantha", "This causes certain screens or editing operations to return incomplete data or fail to save changes."),
        ("Developer A", "Samantha", "Fourth, Static Screen Data. Some financial and business screens still return hardcoded data instead of reading actual database values."),
        ("Developer A", "Samantha", "Fifth, Authentication and Security. Some security issues remain open, including password reset verification and OAuth token verification."),
        ("Developer A", "Samantha", "There are also issues around certain administrative routes and access checks."),
        ("Developer A", "Samantha", "Sixth, Automated Testing. We still don't have a proper automated test suite in the repository. This is a major concern because several important modules have complex dependencies."),
        ("Developer B", "Daniel", "What about the performance and database connection issues? I remember you mentioned that there were some problems with Neo4j connections."),
        ("Developer A", "Samantha", "Yes. There has been an important improvement in the latest code. Previously, the application created a new Neo4j driver connection pool repeatedly, which could lead to connection and memory leaks."),
        ("Developer A", "Samantha", "That has now been changed to one shared connection pool per process. However, other performance issues remain. Some asynchronous routes still execute blocking operations directly, and the full onboarding process can involve approximately one thousand LLM calls."),
        ("Developer A", "Samantha", "The LLM cache also has some limitations because retries can clear the entire cache. So, we still need to work on performance and reliability."),
        ("Developer B", "Daniel", "Understood. We shouldn't assume that something is fixed just because the API returns a successful response."),
        ("Developer A", "Samantha", "Exactly. We need to verify the actual database state and downstream behaviour."),
        ("Developer B", "Daniel", "Before we discuss the action plan, are there any major changes in the latest November update that I should know about?"),
        ("Developer A", "Samantha", "Yes, there are several important changes. First, the Neo4j connection pool has been consolidated into one shared pool."),
        ("Developer A", "Samantha", "Second, nineteen new prompt templates and two system texts were added, and twenty-one new chain IDs were wired into the shared module."),
        ("Developer A", "Samantha", "Third, Otto has received several enhancements, including voice responses, private chat, company-related questions, document knowledge search, more meeting request types and confirmation handling."),
        ("Developer A", "Samantha", "Fourth, the Knowledge Directory and Data Access by Role modules have been added. The application now includes knowledge slash, access slash and search slash functionality, along with report-section APIs and a new programme module."),
        ("Developer A", "Samantha", "Fifth, the OKR import workflow has expanded, including spreadsheet mapping, planning, key-result generation and work-duration estimation. The X-B-R-L accounting workflow has also been rebuilt to use deterministic financial calculations."),
        ("Developer B", "Daniel", "And did these changes resolve the earlier security issues?"),
        ("Developer A", "Samantha", "Some of them, yes. For example, the utils routes now require authentication, and several calendar-related user-ID checks have been implemented. The Neo4j driver issue is also fixed."),
        ("Developer A", "Samantha", "However, password-reset O-T-P verification and OAuth provider-token verification are still open issues. The credential in the commented configuration line also still needs to be rotated."),
        ("Developer A", "Samantha", "So, we should use the latest status rather than relying only on the original handover notes."),
        ("Developer B", "Daniel", "Let's say an AI generation route starts returning incorrect data or fails to generate valid JSON. How should I approach debugging it?"),
        ("Developer A", "Samantha", "We have a troubleshooting approach documented for this. I recommend following these steps. First, identify the API route and its chain ID."),
        ("Developer A", "Samantha", "Second, verify the input data coming from Neo4j. Missing upstream information is one of the most common reasons for incorrect AI outputs. Third, check whether the response is coming from the LLM cache."),
        ("Developer A", "Samantha", "Fourth, inspect the logs for token-limit issues or gateway timeouts. Fifth, validate the JSON structure and ensure that all required keys are present."),
        ("Developer A", "Samantha", "Finally, check whether the database storage function and the downstream reader use the same field names."),
        ("Developer B", "Daniel", "What are the most common failures?"),
        ("Developer A", "Samantha", "There are several. JSON output gets truncated because the response reaches its token limit. Large requests fail with a five-zero-four gateway timeout."),
        ("Developer A", "Samantha", "The model returns invalid JSON or additional text. The output contains an incorrect number of expected items. Required keys are missing or renamed."),
        ("Developer A", "Samantha", "An old cached response appears after a prompt change. A downstream screen shows no data because an earlier pipeline step did not run."),
        ("Developer A", "Samantha", "For example, if the KPI generation step expects a particular number of KPIs but receives fewer, the process can fail its validation."),
        ("Developer A", "Samantha", "We should not immediately change the prompt in every situation. Sometimes the actual issue is in the input data, database storage or API response handling."),
        ("Developer B", "Daniel", "That's a good point. We should isolate the source of the problem before changing anything."),
        ("Developer A", "Samantha", "Exactly. Also, whenever we modify a prompt, we should change one thing at a time, rerun a controlled test and compare the new output against the previously saved output."),
        ("Developer B", "Daniel", "If you had to highlight the most critical technical risks in this project, what would they be?"),
        ("Developer A", "Samantha", "I would put security first. We need to address the following: Password reset currently trusts a client-supplied o-t-p verified value instead of independently validating the O-T-P."),
        ("Developer A", "Samantha", "OAuth login does not properly verify the provider's identity token. The admin slash run-reminders-now route still lacks authentication. Some access-control and user-scoping checks need further review."),
        ("Developer A", "Samantha", "A database credential remains in a commented configuration line and Git history. It must be rotated. Then there are reliability concerns."),
        ("Developer A", "Samantha", "We need to address the one-activity test limits, the missing cognitive-thinking storage, missing database relationships and the absence of automated tests."),
        ("Developer A", "Samantha", "We should also review Otto's database-query generation path because it uses an AI-generated Cypher query that is subsequently executed. The query-validation function needs careful security review."),
        ("Developer B", "Daniel", "And what about the calendar functionality?"),
        ("Developer A", "Samantha", "There is one important issue. Regenerating a calendar with force equals true can silently remove tasks that Otto previously confirmed."),
        ("Developer A", "Samantha", "We need to make sure that confirmed tasks are preserved or handled intentionally when calendars are regenerated. Also, some calendar display labels are still provisional."),
        ("Developer B", "Daniel", "Thanks for explaining everything. If I'm taking ownership of this backend now, what would you recommend I focus on during my first two weeks?"),
        ("Developer A", "Samantha", "I would divide the work into six priorities. Priority one – Security Fixes. Implement proper O-T-P validation for password reset."),
        ("Developer A", "Samantha", "Verify Google, Microsoft and Apple OAuth tokens. Review route-level authentication and authorisation. Verify user IDs against the authenticated user. Rotate the exposed database credential."),
        ("Developer A", "Samantha", "Priority two – Database and Data Integrity. Fix K-P-I and issue IDs so they are unique per user. Correct K-P-I field mismatches. Review missing relationships such as HAS-C-V-P, HAS-OBJECTIVE and HAS-TURN."),
        ("Developer A", "Samantha", "Ensure data is correctly stored and retrieved. Priority three – Pipeline Completion. Remove the one-input test limitation from F-J-A D-two. Remove the one-activity limitation from mental-action generation."),
        ("Developer A", "Samantha", "Implement actual cognitive-thinking scene persistence. Priority four – Performance Optimisation. Review blocking operations inside asynchronous routes."),
        ("Developer A", "Samantha", "Reapply and verify the tested non-blocking execution improvements. Review LLM caching and retry behaviour. Monitor Neo4j connection-pool usage."),
        ("Developer A", "Samantha", "Priority five – Testing. Introduce automated backend tests. Test authentication and access permissions. Test the complete onboarding pipeline."),
        ("Developer A", "Samantha", "Validate AI response structures and database persistence. Test Otto's task extraction and calendar placement. Priority six – Documentation and Maintenance."),
        ("Developer A", "Samantha", "Review the prompt catalogue. Keep the API documentation updated. Remove or clearly isolate obsolete test routes. Review old and duplicate code."),
        ("Developer A", "Samantha", "Document important technical decisions."),
        ("Developer B", "Daniel", "That gives me a clear direction. I think I'll first familiarise myself with main dot py, shared dot py, utils dot py and modular pipeline dot py."),
        ("Developer B", "Daniel", "Then I'll understand the database relationships and review the security issues before making any significant changes. I'll also make sure that any changes are tested against the current main branch rather than relying on older optimisation patches."),
        ("Developer A", "Samantha", "That's the right approach. One additional recommendation: be particularly careful when merging changes. We have previously experienced code being overwritten or old files being reintroduced during merges."),
        ("Developer A", "Samantha", "So, review the Git history and compare the current implementation before removing or replacing anything."),
        ("Developer B", "Daniel", "Understood. I'll also keep the existing output examples as a reference when modifying the AI prompts."),
        ("Developer B", "Daniel", "I think I have a much better understanding of the backend now. To summarise, EarnOut is built around FastAPI, Neo4j and OpenAI, with a large AI-driven business-planning pipeline."),
        ("Developer B", "Daniel", "The core generation features are implemented, and we have additional modules for Otto, meetings, OKR, X-B-R-L, document management and reporting."),
        ("Developer B", "Daniel", "However, our immediate focus should be security, data integrity, completing the remaining pipeline limitations, improving performance and introducing automated testing."),
        ("Developer B", "Daniel", "Is there anything important I've missed?"),
        ("Developer A", "Samantha", "No, that's a good summary. The most important thing is to understand that this is a highly interconnected system."),
        ("Developer A", "Samantha", "A small change in one prompt, database relationship or storage function can affect multiple downstream screens and workflows."),
        ("Developer A", "Samantha", "So, before changing anything, understand the complete flow, verify the existing implementation and test the result."),
        ("Developer A", "Samantha", "The handover manual contains the architecture, API flows, model configuration, prompt catalogue, troubleshooting procedures and the latest status re-check."),
        ("Developer A", "Samantha", "It should be your primary reference while taking over the project."),
        ("Developer B", "Daniel", "Perfect. Thanks for taking the time to explain everything. I'll start by reviewing the security items and then move towards the pipeline and performance improvements."),
        ("Developer A", "Samantha", "Sounds good. Thanks, and welcome to the project."),
        ("Developer A", "Samantha", "Meeting concluded."),
    ]

    GAP = 0.45  

    segments = []
    concat_list = []
    clock = 0.0

    for i, (speaker, voice, text) in enumerate(LINES):
        if use_elevenlabs:
            mp3_part = parts / f"{i:02d}.mp3"
            wav_part = parts / f"{i:02d}.wav"
            synthesize_elevenlabs(text, dev_a_voice if speaker == "Developer A" else dev_b_voice, mp3_part)
            run(["ffmpeg", "-y", "-i", str(mp3_part), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_part)])
            audio_part = wav_part
        else:
            audio_part = parts / f"{i:02d}.aiff"
            synthesize_say(text, voice, audio_part)

        dur = duration(audio_part)
        segments.append({
            "id": f"gt-{i:02d}",
            "speaker": speaker,
            "text": text,
            "t_start": round(clock, 2),
            "t_end": round(clock + dur, 2),
        })
        concat_list.append(f"file '{audio_part}'")
        clock += dur
        concat_list.append(f"file '{silence}'")
        clock += GAP

    list_file = parts / "concat.txt"
    list_file.write_text("\n".join(concat_list) + "\n")
    raw = parts / "raw.wav"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c:a", "pcm_s16le", str(raw)])

    wav = OUT / "sample_meeting.wav"
    mp3 = OUT / "sample_meeting.mp3"
    run(["ffmpeg", "-y", "-i", str(raw), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
    run(["ffmpeg", "-y", "-i", str(raw), "-ac", "1", "-ar", "44100", "-b:a", "128k", str(mp3)])

    total = duration(wav)
    source_note = "ElevenLabs" if use_elevenlabs else "macOS say"
    payload = {
        "title": "EarnOut Backend – Project Handover Meeting",
        "source": f"synthetic demo meeting generated with {source_note}",
        "duration_s": round(total, 2),
        "language": "en",
        "speakers": ["Developer A", "Developer B"],
        "segments": segments,
        "notes": [
            "Ground-truth timestamps are used for the alignment fallback and for evaluating ASR lag.",
            "The content covers the complete EarnOut Backend handover meeting between Developer A and Developer B.",
            f"Voice generation: {source_note} (Developer A={dev_a_voice}, Developer B={dev_b_voice}).",
        ],
    }
    (OUT / "sample_meeting.transcript.json").write_text(json.dumps(payload, indent=2))
    print(f"wrote {wav} ({total:.1f}s), {mp3}, and {len(segments)} transcript segments")

if __name__ == "__main__":
    if not ELEVEN_API_KEY and sys.platform != "darwin":
        sys.exit("No ELEVEN_LABS_API_KEY set and not on macOS. Either set the API key or supply your own sample audio.")
    main()