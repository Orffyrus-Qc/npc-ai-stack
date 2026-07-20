# NPC AI Stack — single machine, 8–12GB GPU

Realistic, learning NPCs for a Hytale server: evolving personality, two-tier
memory, sandboxed skill self-improvement, all on one box with one small GPU.

## Layout

```
docker-compose.yml          the whole stack (llama.cpp + orchestrator + DBs)
orchestrator/
  main.py                   WebSocket gateway + event handlers + compression daemon
  priority_queue.py         GPU slot arbiter: dialogue always wins, ambient never blocks
  llm_client.py             prompt builder (personality+memory -> system prompt) + llama.cpp client
  memory.py                 Qdrant episodic + Postgres semantic facts + compression
  personality.py            bounded trait nudges, per-player trust, decay to baseline
sandbox/
  run_skill_validation.sh   ephemeral locked-down containers for candidate skills
  skill_harness.py          the tests a skill must pass to be promoted
scripts/download_model.sh   fetch Qwen2.5-7B-Instruct GGUF
NpcAiBridge.java            plugin-side transport (no Hytale API coupling)
```

## Bring-up

```bash
# 1. host prereqs (once)
sudo apt install nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker

# 2. model (Q4_K_M for 8GB, pass q5_k_m for 10-12GB)
./scripts/download_model.sh

# 3. stack
docker compose up -d --build

# 4. smoke test the LLM directly
curl -s http://localhost:8080/v1/chat/completions -d '{
  "messages":[{"role":"user","content":"Say hello as a grumpy blacksmith."}],
  "max_tokens":40}' | python3 -m json.tool
```

Plugin connects to `ws://<host>:8765`. Protocol is documented at the top of
`orchestrator/main.py`.

## Tuning knobs

| Symptom | Knob |
|---|---|
| Dialogue feels slow under load | lower `--parallel` to 3 (fewer, faster slots) or shrink `max_tokens` |
| NPCs need longer memory context | `--parallel 2` + bigger per-slot ctx (concurrency<->context tradeoff) |
| World sim stutters during dialogue | trim `llm-inference` `cpus:` or pin hytale-server cores |
| Ambient chatter never fires | raise `ambient_max_in_flight` (costs dialogue headroom) |
| OOM on 8GB card | switch model to the 3B (Llama-3.2-3B / Qwen2.5-3B Q5_K_M) |

## Skill self-improvement flow

1. A meta-agent (or you) writes candidate `decide(state)->action` skills to `sandbox/candidates/`.
2. Cron runs `run_skill_validation.sh` during low-player windows:
   each candidate executes in a `--network none --read-only` throwaway container
   against `skill_harness.py`.
3. Pass -> timestamped copy in `approved/` (the registry the orchestrator loads).
   Fail -> `rejected/` with logs. Every rejection is a future test case.
4. Rollback = delete the newest approved version.

The live loop can always adjust *facts* and *personality*; only *behavior
code* goes through the sandbox gate. That split is what keeps a
self-improving NPC from becoming a self-sabotaging server.
