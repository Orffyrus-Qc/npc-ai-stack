# OpenHands-style NPC AI Brain (Hytale)

**Status:** scaffolded 2026-07-22 inside `npc-ai-stack-git`  
**Inspiration:** [OpenHands](https://github.com/OpenHands/OpenHands) / [Software Agent SDK](https://github.com/OpenHands/software-agent-sdk)  
**Goal:** An NPC that **learns Hytale by itself**, can **play like a player**, reads **real game files + map saves**, uses **wiki/web**, **asks the player for help**, and **helps the player** using the same tools.

This is **not** a pip install of `openhands-sdk` inside the game loop (too heavy, wrong sandbox).  
It is the **same architecture** adapted to your existing orchestrator + GPU slot rules.

---

## 1. What OpenHands gives us (mapped)

| OpenHands concept | This stack |
|-------------------|------------|
| **Agent loop** (LLM → tool → observation → repeat) | `orchestrator/agent_brain/loop.py` → `AgentLoop` |
| **Action / Observation** | `agent_brain/types.py` |
| **Tools** (bash, file edit, browser…) | Hytale tools: game files, map, wiki, web_fetch, ask_player |
| **Workspace** | Read-only mounts: `%APPDATA%\Hytale`, plugin `run/` |
| **Conversation / state** | `BrainSession` + episodic/semantic memory + `npc_experience` |
| **Skills** | Existing `sandbox/approved/` + new **lessons** table |
| **Human-in-the-loop** | `ask_player` / `answer_help` tools |
| **Reinforcement learning** | Experience replay with **shaped rewards** (not GPU RL training of a new net) |

### What “reinforcement learning” means here

We are **not** fine-tuning Qwen with PPO on every step (that would destroy the 12GB budget and the live server).

We **are** doing:

1. **Try** a tool / play proposal  
2. **Observe** success/failure (+ shaped reward)  
3. **Store** `(goal, action, observation, reward)` in Postgres  
4. **Prefer** high-reward lessons on the next prompt (experience-guided policy)

That is the same outer loop OpenHands uses for software tasks: **reward from the environment**, policy = LLM + memory.

Later you *can* export `npc_experience` for offline preference fine-tuning — out of scope for v1.

---

## 2. System diagram

```
                    ┌─────────────────────────────────────┐
                    │         Human player (chat)         │
                    └──────────────┬──────────────────────┘
                                   │ dialogue / help
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Hytale plugin (NpcAiStack)                                          │
│  TalkToAI / PlayerChat → WebSocket → say / offer_guide / companion  │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ ws://localhost:8765
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Orchestrator (main.py)                                               │
│  help? → AgentLoop (OpenHands brain)                                 │
│  else  → LLM.dialogue (fast RP path)                                 │
│  idle  → brain_learn_daemon (curriculum on ambient GPU slots)        │
└───┬──────────────┬──────────────┬──────────────┬─────────────────────┘
    │              │              │              │
    ▼              ▼              ▼              ▼
 llama.cpp      Qdrant        Postgres       Tools (read-only)
 (Qwen 7B)     memory        experience     game files / map / wiki / web
```

---

## 3. Tools (player-like “hands”)

| Tool | Purpose |
|------|---------|
| `search_game_files` | Find assets by name under Assets.zip / UserData / Client / Server |
| `read_game_file` | Read JSON/text (e.g. `Assets:Server/Item/Items/Bench/Bench_Campfire.json`) |
| `list_game_tree` | Directory / zip listing |
| `read_map_markers` | World markers so NPC + player navigate together |
| `read_world_config` | World config from saves / plugin run universe |
| `search_wiki` | Local ingested wiki (already in stack) |
| `web_fetch` | Optional HTTPS (allowlisted; `BRAIN_ALLOW_WEB=1`) |
| `ask_player` | NPC asks human for help (HITL) |
| `answer_help` | Grounded answer to player after research |
| `propose_play_action` | Act like a player: gather/craft/go_to/explore/… |
| `record_learning` | Durable lesson + reward |
| `finish` | End loop |

Path aliases for files:

- `Assets:Server/Item/Items/...`
- `UserData:Saves/...`
- `Client:Data/...`
- `PluginRun:universe/worlds/default/...`
- `Game:Server/HytaleServer.jar` (path only — prefer Assets for content)

---

## 4. When the brain runs

### A) Player asks for help (dialogue route)

If player text matches help-like patterns (`how do`, `where is`, `recipe`, `craft`, `help me`, `wiki`, `map marker`, …):

→ multi-step `AgentLoop` with **dialogue** GPU priority  
→ reply is research-backed  
→ stored in episodic memory (+ fact if high reward)

Ordinary RP still uses the single-call `LLM.dialogue` path (latency-friendly).

### B) NPC self-learns (curriculum daemon)

Every `BRAIN_LEARN_INTERVAL_S` seconds (default 900):

→ pick next curriculum goal (crafting, interactions, combat JSON, drops, map, …)  
→ run short loop on **ambient** GPU slots only  
→ write lessons + optional semantic facts  

Never steals dialogue slots (hard rule of this stack).

### C) NPC stuck → ask player

Tool `ask_player` ends the loop with a spoken question — co-op discovery.

---

## 5. Enable on your machine

### 5.1 `.env` (repo root)

```env
HYTALE_HOST_PATH=C:/Users/Orffyrus/AppData/Roaming/Hytale
PLUGIN_RUN_HOST_PATH=D:/CLAUDE_AI/npc-ai-stack-git/hytale-plugin/run
BRAIN_ALLOW_WEB=0
BRAIN_MAX_STEPS=6
BRAIN_LEARN_INTERVAL_S=900
```

Copy from `.env.example`.

### 5.2 Rebuild orchestrator

```bash
docker compose up -d --build orchestrator
```

Confirm logs:

```
orchestrator listening on :8765 (OpenHands-style brain enabled)
experience store ready
```

### 5.3 In-game tests

1. Talk to Adventurer NPC:  
   `How do I craft at a campfire?`  
   → should search Assets, read campfire/recipe JSON, answer with `answer_help`.
2. `Where are map markers on our world?`  
   → `read_map_markers`.
3. NPC may later self-study and mention lessons when asked casually.

---

## 6. Package layout

```
orchestrator/agent_brain/
  __init__.py
  types.py           # Action, Observation, Experience, tool specs
  config.py          # mounts + feature flags
  loop.py            # AgentLoop
  prompts.py         # JSON tool-call system prompt
  curriculum.py      # self-learning goals
  experience.py      # Postgres npc_experience + npc_lessons
  tools/
    registry.py      # dispatch
    game_files.py    # Assets.zip + FS (allowlisted)
    map_world.py     # saves / plugin universe
    web.py           # optional allowlisted HTTPS
```

Wire-in: `orchestrator/main.py` (`handle_brain_help`, `brain_learn_daemon`).

---

## 7. Safety (aligned with CLAUDE.md hard rules)

1. **Dialogue beats ambient** — self-learn uses ambient only.  
2. **No game-loop blocking** — all LLM calls still go through dispatcher timeouts.  
3. **Read-only game mounts** — tools cannot write into Hytale install.  
4. **Skill code still sandboxed** — brain lessons are facts/experience, not hot-loaded Python skills.  
5. **Web off by default** — `BRAIN_ALLOW_WEB=0`.  
6. **Path jail** — only under `HYTALE_ROOT` / plugin-run.

---

## 8. Roadmap (next slices)

| Priority | Slice | Status |
|----------|--------|--------|
| P0 | Live verify help path with Assets mount + real player question | Tools **smoke-tested** on real Assets.zip 2026-07-22; full Docker+in-game still open |
| P1 | Plugin enacts `propose_play_action` via GuideState | **Done 2026-07-22** — `play_action`/`play_target` wire + `PlayIntentState` |
| P1 | Inject top lessons into normal `LLM.dialogue` system prompt | **Done 2026-07-22** — `NPCContext.lessons` + `LESSONS_TEMPLATE` |
| P2 | Export experiences → offline DPO/SFT preference pairs | open |
| P2 | Optional OpenHands Agent Server as **sidecar** for heavy research jobs | open |
| P3 | Multi-NPC parallel brains with per-npc curriculum progress | open |
| P3 | Actually gather/mine blocks (not only walk toward keyword) | open |

### Wire fields added on `say` (2026-07-22)

```json
{
  "type": "say",
  "play_action": "go_to|explore|gather|mine|rest|fight|…",
  "play_target": "keyword or empty"
}
```

Plugin: `NpcAiBridge` → `PlayIntentState.applyFromOrchestrator` → often `GuideState.startGuidingFromKeyword`.

---

## 9. Relation to full OpenHands install

You can still run [OpenHands Agent Canvas](https://github.com/OpenHands/OpenHands) on the side for **dev** (modding, debugging plugins).  
The **in-game NPC** uses this lighter brain so it:

- shares the same GPU queue as dialogue  
- speaks through the existing WebSocket protocol  
- never requires Node/Agent Canvas to play

If you later want full OpenHands tools against the mod repo:

```text
OpenHands workspace = D:\CLAUDE_AI\npc-ai-stack-git
NPC brain workspace = mounted Hytale install + saves
```

Two agents, two jobs — same philosophy.

---

## 10. Prompt for future AIs working on this

```
You are extending the Hytale NPC OpenHands-style brain in
D:\CLAUDE_AI\npc-ai-stack-git\orchestrator\agent_brain\.

Rules:
- Keep Action→Observation→Experience loop.
- Never block dialogue GPU with self-learn.
- Game file tools stay read-only + path-jailed.
- Prefer Assets.zip JSON as ground truth over inventing mechanics.
- Skill Python still goes through sandbox/; brain only writes lessons/facts.
- Document honesty: confirmed vs untested.
```
