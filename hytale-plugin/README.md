🐄 **Falling Cow Zone** — see the [repo root README](../README.md).

## 2026-07-22, later still: found the real "dropped context" bug - a mangled Unicode escape

Two earlier fixes today (trailing-tag hallucination, real-world wiki
content) didn't actually explain a live report: `"Emerald Wildsu2014I've
got a feeling..."` - no space, `u2014` glued onto the adjacent word.
That's `—` (the em-dash Unicode escape) with its backslash gone missing,
confirmed by reading the real server log rather than guessing again.

Root cause on both sides of the wire: `main.py`'s `json.dumps()` used the
default `ensure_ascii=True`, escaping the model's stylistic em-dash as
`\uXXXX`; `NpcAiBridge.java`'s hand-rolled `extract()` only ever handled
single-char escapes (`\"` -> `"`, `\\` -> `\`) - a Unicode escape has `u`
right after the backslash, so it fell into that branch, kept just the
`u`, and left the four hex digits (`2014`) as plain text. Fixed both:
`ensure_ascii=False` on the Python side (sends raw UTF-8, sidesteps the
gap for any non-ASCII character), and `extract()` now properly decodes
`\uXXXX` plus `\n`/`\r`/`\t`.

Verified via a reflection-based test harness calling the real compiled,
private `extract()` method with the exact reported input - confirmed the
real em-dash character comes through with no mangled text, and existing
escapes (`\"`, `\\`) still work. `./gradlew build`/`runServer` clean. A
near-miss caught mid-deployment: two `java.exe` processes were running
after the boot-test - checked `CreationDate`/`CommandLine` before
touching either, since one turned out to be the user's own real, active
Hytale session (not a leftover test server) - left both alone. That live
session won't pick up this fix until restarted (plugin jars aren't
hot-reloaded).

## 2026-07-22, later still: companion no longer stares at its owner mid-fight

Live-tested report: "when npc is in combat mode then disable npc looking at
compagnon player." Root cause confirmed via disassembly, not assumed: the
Watching state's player-watch node (`Continue: true`, `HeadMotion: Watch`)
runs every tick regardless of what else matches, and the combat nodes set
their own competing `HeadMotion: Watch` (targeting the locked hostile) -
`HeadMotionWatch.computeSteering()` resolves its target from whichever
node's own Sensor context is active, with no verified guarantee about which
of two same-tick assignments wins. Fixed by making the player-watch node
simply not match at all while a hostile is locked (`And`+`Not`+`Target,
Range: 999` - confirmed via disassembly of `SensorTarget.matches()` that
`Range` checks distance to whatever's already locked, so a large value
means "anything locked, regardless of distance") rather than relying on
unverified priority semantics. Also added `HeadMotion: Watch` to the chase
node (melee already had one, chase didn't) so the companion looks at the
hostile while closing distance, not nothing. Boot-tested via a real
`./gradlew runServer` boot - clean validation, jar rebuilt and reinstalled.
Not yet live-confirmed (needs a real fight to watch during).

## 2026-07-22: re-scoped to a single NPC (Adventurer only)

Project re-planned around a single NPC. `Elder_Miri.json`, `Merchant_Oskar.json`,
`AI_Talker.json`, and the shop system only `Merchant_Oskar` used
(`OpenShopIfRequestedAction`/`Builder`, `PendingShopOpen.java`, the
`open_shop`/`OPEN_SHOP` action and its shop-availability prompt lines) are
deleted. Test instructions below now spawn `Adventurer` only. The dated
entries below this one describe the 4-NPC era as it was actually built and
tested - left as accurate history, not current state. See CLAUDE.md's
matching 2026-07-22 entry for the full reasoning.

## 2026-07-22, later: companion now follows its actual owner, not the nearest player

"Max 1 companion per player" was already true at the DB level
(`taming.py`'s unique-owner constraint), but the Java-side follow/defend
sensors matched the **nearest** player regardless - `CompanionState`'s own
javadoc flagged this as a known v1 simplification, fine solo, wrong the
moment another player is nearer than the real owner. Fixed:
`CompanionState` now stores the owner's real `UUID`, and a new
`EntityFilterIsOwner` entity filter (registered as `"IsOwner"`, same shape
as `EntityFilterHostileSpecies`) restricts both of `Adventurer.json`'s
companion-follow blocks (Watching + `$Interaction`) to specifically that
player - confirmed via disassembly that the built-in `"Player"` sensor's
builder extends the same `Filters`-supporting base `"Mob"` does.

The reactive-defense `EntityEvent` sensor (widened 45-block hostile search
when a nearby player takes damage) is **not** owner-scoped - confirmed via
disassembly it has no candidate-iteration/Filters mechanism at all (a
slot/subscription check, not an entity search). Documented as a permanent,
harmless limitation: worst case is an extra search triggered by a
non-owner being hit, not a wrong attack target (still gated by
`IsHostileSpecies` regardless).

**Boot-tested via a real `./gradlew runServer` boot** - `Loaded 974 NPC
configurations, Generic: 1`, `Validation complete.`, `Hytale Server
Booted!`, no errors tied to the new `Filters`/`IsOwner` additions - **not
yet live-confirmed** (needs two real connected players to verify the
companion follows its owner and ignores a nearby stranger).

## 🎉 2026-07-21: guide-to-landmark confirmed working live, after 5 real bugs found by testing it

The guide-to-landmark feature below (and the companion-follow/combat
features from the same day) went through a full round of real, live
testing with a connected player - not just boot-testing - and every
single bug found was a genuine mechanism-level mistake, not something a
code review would have caught:

1. **Wrong parameter order to `SpiralSearchUtil.search()`.** Assumed
   `(x, y, z, radius)` from the call shape; disassembling the real
   `LocateZoneCommand`/`AbstractLocateSubcommand` (whose own compiled
   `LocalVariableTable` names the args `seed`, `playerX`, `playerZ`)
   showed the true signature is `(seed, x, z, radius)` - a pure 2D
   search. The bug silently searched near world origin instead of near
   the NPC, producing wildly wrong "nearest landmark" coordinates -
   confirmed live when an NPC was told to guide toward a point over 1500
   blocks from its actual position.
2. **`docker compose restart` doesn't rebuild the image.** A companion-
   resync fix was written, "deployed," and live-tested as still broken -
   because `restart` reuses the existing image, silently leaving the old
   code running for a full test cycle. `docker compose up -d --build
   <service>` is required after any orchestrator code change; verified
   after the fact by grepping the running container's `/app/*.py` for
   the new code.
3. **Water search radius too small.** `SEARCH_RADIUS` (400 blocks,
   tuned for biome-region zones) was reused for Ocean/Shallow_Ocean/Shore
   search too, but coastline is far sparser than biome zones - a
   `NEAREST_WATER` guide request reliably found nothing at all for an
   inland player. Given a separate, much larger `WATER_SEARCH_RADIUS`
   (3000 blocks).
4. **`$Interaction` had zero movement instructions.** The state an NPC
   enters for ~1s every time a player clicks to start talking had no
   `BodyMotion` node at all (unlike `Watching`), so an established
   companion visibly froze for that second on every click. Added the
   same companion-follow node to `$Interaction`.
5. **No timeout on guiding.** The guide node has no `Continue` in
   `Watching`, so it takes absolute priority over companion-follow for
   as long as it keeps matching. Since almost every reply decided
   `OFFER_GUIDE` (even generic "I'll follow you" lines), and nothing
   capped how long the NPC would keep beelining toward a landmark, a
   companion could stop following the player *permanently* after a
   single conversation - reported live as "he follows me at first but
   won't follow after I talk to him." Fixed with a 25s give-up timeout
   in `GuideState`/`SeekLandmarkSensor`.

Also fixed from the same live-testing round: NPCs repeating the exact
same line verbatim across separate turns (added `repeat_penalty`/
`presence_penalty` to the llama.cpp call, plus an explicit "don't repeat
yourself" prompt rule), and the dispatcher's flat `"..."` GPU-busy
fallback replaced with a rotating pool of in-character "give me a
moment" lines. **Superseded 2026-07-21**: those lines (and the whole
canned-fallback concept) were removed - see CLAUDE.md's "removed every
pre-written fallback line" - an NPC with nothing to say now just says
nothing, rather than a pre-written filler pretending to be its own words.

**Known simplification, stated plainly:** companion combat (below) is
tied purely to "is this NPC a companion + is a hostile nearby," not to a
specific in-conversation "let's fight" request - a tamed companion will
engage any hostile it notices while following, regardless of what was
actually said.

**2026-07-21: real bug found and fixed - companion combat never actually
attacked anything.** First live test (tamed companion, real hostile mob
present) and it never engaged. Root cause: the Mob sensor's `Prioritiser`
filtered for `AttitudesByPriority: ["Hostile"]` only. Confirmed via
disassembly of `SensorEntityPrioritiserAttitude.getPriority()` that
`WorldSupport.getAttitude()` resolves a *pairwise* relationship between
this specific NPC and the candidate entity, not "is this creature type
hostile" in the abstract - and any attitude not in the priority list gets
filtered out entirely before it's even considered (an unlisted attitude
reaching that code throws `IllegalStateException`, so it must be excluded
upstream). A mob configured hostile toward players is not automatically
flagged Hostile toward another NPC like Adventurer - it likely resolves to
Neutral instead. The real, shipped analog for "is this nearby thing
dangerous" (`Component_Instruction_Damage_Check.json`'s Sight/Sound-by-
Attitude sensors, used by the neutral Kweebec to decide when to flee)
checks `[Hostile, Neutral]`, not `Hostile` alone - for exactly this
reason. Fixed to match. **Boot-tested clean, not yet re-confirmed live**
(needs the next play session with a real hostile nearby). Possible
side effect worth watching for: this may now also make a companion react
to genuinely harmless Neutral creatures (passive wildlife), the same
trade-off the real game's own Kweebec fear-check already accepts.

**2026-07-21, correction: the attitude fix above was never actually
exercised.** Reported still not attacking. This machine turned out to be
the one the real session runs on - found real, timestamped server/client
logs (`%APPDATA%/Hytale/UserData/Saves/*/logs/`, `Logs/`) and cross-
referenced them against `docker compose logs orchestrator` for the same
window. The real trace: player clicked the already-tamed Adventurer, sent
one chat message that got classified `action=offer_guide` (reply text:
*"I'll follow close behind, Orffyrus. Lead the way."* - the NPC saying it
would follow the player, tagged with the action that makes it walk away
from the player instead), `GuideState` sent it to a landmark, it arrived 2
seconds later, and the session ended ~2.5 minutes after with nothing else
logged. The companion had physically left before any hostile encounter
could happen near it - never an attitude-filter problem. See CLAUDE.md's
"combat STILL didn't fire" section for the actual root cause (an ambiguous
prompt rule confusing "guide me somewhere" with "follow/accompany me") and
the fix, verified against the real model. The attitude fix above may well
be entirely correct - it just never got the chance to run in this session.

**2026-07-21, real root cause found: missing `AttitudeGroup`.** Reported
again, still not attacking. Direct comparison against real shipped role
files (`Trork_Warrior.json`/`Template_Trork_Melee.json`, and `Kweebec_
Rootling.json`/`Template_Kweebec_Sapling.json` - Adventurer's own exact
appearance) found every real combat-capable creature declares a top-level
`"AttitudeGroup"` field; none of our 4 custom roles declared one at all.
Fetched the actual asset definitions (`Server/NPC/Attitude/Roles/...`):
Trork's own group is explicitly `{"Hostile": ["Kweebec"], ...}` - hostile
toward the literal string "Kweebec", never toward "Player" (that's a
separate `DefaultPlayerAttitude` field). `WorldSupport.getAttitude()` was
never going to resolve to Hostile *or* Neutral for an NPC with no group to
match in the first place - overturning the premise of the earlier
`[Hostile, Neutral]` fix, which may still be correct as defense-in-depth
but was never the actual problem. Fixed: added `"AttitudeGroup":
"Kweebec"` to `Adventurer.json` only, matching its real appearance and
Trork's own Hostile list verbatim - deliberately not added to the other 3
roles, since the real, shipped `Klops_Merchant.json` (Merchant_Oskar's own
appearance) has no `AttitudeGroup` either; it's genuinely optional and
only matters for creatures meant to participate in hostile/friendly
targeting. Boot-tested clean (would have failed validation if "Kweebec"
weren't a real registered asset). Rebuilt jar installed. Not yet
re-confirmed live.

**2026-07-21: reactive defense added** (requested: "npc must fight with me
if I am attacked"). The Mob+Attitude sensor above only reacts to a hostile
this companion can already see/sense itself - if something attacks the
player from outside the companion's own detection, it wouldn't previously
notice at all. Added a second, real, shipped sensor - `"Type":
"EntityEvent"` (`EventType: Damage`, `SearchType: PlayerOnly`, `NPCGroup:
Player`) - confirmed via disassembly of `SensorEntityEvent`/
`BuilderSensorEntityEvent` and via its real usage in
`Component_Instruction_Allied_Damage_Check.json`. It fires the instant the
followed player takes damage nearby, regardless of the companion's own line
of sight, combined with the same hostile search widened to 45 blocks (vs.
the normal 30) since a threat is now known to be imminent. **Boot-tested
clean** (zero validation errors); **not yet live-confirmed** - same caveat
as the combat system it extends, no live test session has happened near a
real hostile yet. Adventurer-only for now (the archetype where physical
combat fits the character) - Elder_Miri/Merchant_Oskar remain non-combat
companions by design, matching their low-aggression personas.

**2026-07-21, later still: "npc must be hostile to all enemy" - the
`AttitudeGroup` fix above only ever covered Trork.** Fetched the real
shipped `AttitudeGroup` assets for other hostile species to check: Goblin's
own `Hostile` list is `[]` (empty), Outlander's group has no `Hostile` key
at all, Scarak is hostile only toward `"Feran"`. Only Trork's own group
happens to name `"Kweebec"` (Adventurer's `AttitudeGroup`) - the previous
fix wasn't wrong, it was just narrower than it looked, because
`AttitudeGroup` encodes narrow per-species ecological rivalries (Trork vs.
Kweebec, Scarak vs. Feran), not a general "is this dangerous" property.
Disassembled `AttitudeView.getAttitude()` to find the *real* resolution
order: (1) a per-entity override, then (2) the `AttitudeGroup` pairwise
lookup above, then (3) - **if neither matched - the fallback is the
OBSERVER's own `DefaultNPCAttitude`, not anything about the candidate.**
So Adventurer looking at a Goblin falls through all the way to its own
`"Ignore"` default and gets silently excluded, same as before, just for a
different underlying reason than the missing `AttitudeGroup` field. There
is no single group value that fixes this generally - the shipped rivalries
are genuinely inconsistent, and editing the shipped monster asset files
isn't an option. Fixed properly this time: added a custom entity filter,
`EntityFilterHostileSpecies.java` (registered as `"IsHostileSpecies"`),
that reads the *candidate's own* `DefaultPlayerAttitude` directly instead
of going through the pairwise resolver - the one flag every dangerous
vanilla mob sets consistently, since it's literally what makes it attack
players at all. Wired into both `Mob` sensor blocks in `Adventurer.json`
via `"Filters": [{"Type": "IsHostileSpecies"}]`, with
`AttitudesByPriority` widened to all five `Attitude` values so the
Prioritiser's own implicit inclusion filter (confirmed via disassembly of
`BuilderSensorWithEntityFilters.getFilters()` - the Prioritiser
auto-generates a filter from its own priority list) never rejects a
candidate this filter already matched; it now only ranks among survivors.
`gradlew build` clean, jar rebuilt and installed. **Not yet live-confirmed
against a non-Trork hostile** (Goblin/Outlander/Scarak) - needs the next
play session.

## 2026-07-21: NPCs can actually lead you to a landmark, not just describe it

Requested: once an NPC follows the player, the next step is for it to be
able to *lead* the player somewhere - e.g. "lead me to the closest river" -
by actually walking there, not just talking about it.

**The mechanism.** When the AI decides `OFFER_GUIDE`, `GuideState.
startGuiding(npcId)` now flips a persistent flag (new `GuideState.java`,
same `ConcurrentHashMap`-per-npcId shape as `CompanionState`). A new
`SeekLandmark` sensor (`SeekLandmarkSensorBuilder`/`SeekLandmarkSensor`)
checks that flag every tick and, if set, hands `NearbyLandmarks.
closestPosition(npcId)` (a `Vector3i` the existing zone-search code -
already used to *describe* the nearest landmark in conversation - was
extended to also cache raw, not just formatted-string) to a paired
`"Type": "Seek"` `BodyMotion` as a target coordinate. The sensor
auto-stops matching (and calls `GuideState.stopGuiding`) once the NPC is
within 6 blocks of the target, so no separate arrival-check node is
needed. All four roles got one new Instructions node for this, placed
after combat (Adventurer only) but before the companion-follow-player
node, so guiding takes priority over idly following but combat still
wins if a hostile is being fought.

**The key discovery**: `PositionProvider` (the same concrete class
`NoteNearbyThreatAction` and friends already use to read a Sensor's
target) has a public no-arg constructor and a `setTarget(double, double,
double)` overload for raw coordinates, and - confirmed via disassembly
(`InfoProviderBase implements InfoProvider`) - is itself directly usable
as a Sensor's `getSensorInfo()` return value. So a custom Sensor can hand
a fixed world coordinate, not just a matched entity, to a paired `Seek`
BodyMotion. No pathfinding API was written or needed; `Seek` is the same
real, shipped movement primitive used for chase/follow (see the section
below).

**A real bug found by the boot-test, not by guessing**: the first version
failed role validation on all four files with `At least one of required
features player target, NPC target, dropped item target, vector position
must be provided at ...BodyMotion|Seek`. Disassembled the engine's own
`RequiresOneOfFeaturesValidator` and `BuilderBase` to find the real
mechanism: a Sensor builder must explicitly call `provideFeature(Feature.
Position)` during `readConfig()` to declare (at load time, not runtime)
that it can supply a vector position - confirmed by finding the exact
same call in the real, shipped `BuilderSensorReadPosition`. Added the same
call to `SeekLandmarkSensorBuilder.readConfig()`; the boot-test then came
back clean on all four roles.

Boot-tested clean (no `FAIL`/`SEVERE` tied to `SeekLandmark` across
`Adventurer.json`, `AI_Talker.json`, `Elder_Miri.json`,
`Merchant_Oskar.json`; server boots and the plugin enables) - **not yet
confirmed live**: needs a real player to ask an NPC to guide them
somewhere and watch it actually walk there and stop on arrival.

## 2026-07-21: adventurer companions can actually fight, and move faster

Requested: can the adventurer actually fight alongside the player, and can
companions keep pace when the player sprints.

**Faster movement.** `MaxWalkSpeed` bumped 5 → 8 on all four roles'
`MotionControllerList`, and the companion-follow `Seek` speed bumped
0.8 → 0.9, giving more headroom to keep pace with a sprinting player.

**Real combat, not scripted.** Found the real, shipped `Attack` action
(`BuilderActionAttack`/`ActionAttack`, confirmed via disassembly, with two
real usage examples in shipped combat JSON -
`Component_Instruction_Attack_Sequence_Spirit.json` and
`Component_Instruction_Intelligent_Idle_Goblin.json`). `Adventurer.json`'s
existing hostile-detection sensor (`"Mob"` + `"Attitude"` prioritiser,
already built for conversation-context awareness) now also sets
`"LockOnTarget": true`, making the detected hostile the locked `"Target"`
two new priority-ordered Instructions nodes react to: chase (`BodyMotion:
Seek` toward the target, full speed, up to 20 blocks) and attack (`"Type":
"Attack", "Attack": "Root_NPC_Attack_Melee"` once within 3 blocks - the
same generic melee attack pattern the real, shipped `Tamed_Cow.json` uses
defensively, not a made-up interaction id). Both gated on `IsCompanion`,
and both take priority over merely following the player (no `Continue`,
so combat wins whenever a hostile is locked).

**Simplification, stated plainly**: combat engagement is tied to
companion status + hostile presence, not to the specific per-message
`OFFER_FIGHT`/`OFFER_GUIDE` decision the AI makes in conversation (that
decision still shapes what the NPC *says* about helping, via
`llm_client.py`'s rules) - a tamed companion will engage any hostile it
detects while following, regardless of what it said the last time it was
asked. Wiring actual combat willingness to the specific decision is a
reasonable follow-up if the difference ever matters in practice.

Boot-tested clean (no validation errors for the new `Attack`/`Target`/
`LockOnTarget` JSON, referencing a real attack pattern) - **not yet
confirmed live with an actual fight against a real hostile creature**,
since that needs a connected player near a real monster to watch.

## 2026-07-21: companions can actually move now - real Seek/follow behavior

Requested: a tamed NPC that agrees to follow the player should actually
be able to move, not just stand there having agreed to it.

**Found a much simpler real mechanism than the raw pathfinding API
flagged as the big remaining lift in every earlier session.** Rather than
wire up `AStarWithTarget`/`PathFollower` directly (real, but low-level,
multi-tick plumbing), found that Hytale's own tamed-animal system
(`Server/NPC/Roles/Creature/Livestock/Tamed/*.json`, e.g. `Tamed_Cow.json`)
already solves "move toward/stay near a target" with two declarative,
JSON-authorable `BodyMotion` types: `"Seek"` (walk toward whatever the
paired `Sensor` currently targets) and `"MaintainDistance"` (stay within
a distance range) - confirmed via `Template_Livestock.json`, the real
template those tamed-animal roles reference. No custom pathfinding code
needed at all.

**New `IsCompanion` sensor** (registered the same way as the custom
Actions - `registerCoreComponentType` is generic over both Sensors and
Actions), paired with the built-in `"Player"` sensor via a real `"And"`
composite (confirmed shape from a real shipped Goblin panic-behavior
JSON). Once `CompanionState.markCompanion(npcId)` is set - flipped the
moment `action="accept_tame"` arrives, already server-enforced by then,
same as the shop gating - the Watching state's fallback `BodyMotion:
Nothing` is replaced by `BodyMotion: Seek` toward the nearest player.

**Known v1 simplification, documented rather than silently assumed**:
this follows the *nearest* player, not specifically its own owner -
`CompanionState` only tracks "is this NPC a companion at all", the same
simplification already used for shop-gating (`is_tamed_by_anyone`).
Correct for solo/small-group testing (everything in this project has
been tested that way); a populated multiplayer world with several
players or several simultaneous companions would need real owner-
specific entity filtering to behave correctly - noted directly in
`CompanionState.java`'s javadoc as the thing to build if that need ever
comes up.

Boot-tested clean across all four roles (the new sensor/motion
combination validates with no errors or warnings) - **not yet confirmed
live with an actual companion visibly walking behind a connected
player**, since that needs a real client to watch.

## 2026-07-21: shop-open bug found live - $Interaction is too short-lived

Live testing of the just-shipped shop feature found a real bug: the
merchant correctly *said* "Of course, let me show you what I've got
today" (proving the AI decision layer worked end to end), but the actual
shop UI never opened.

**Root cause**: `OpenShopIfRequestedAction` lived in the `$Interaction`
state, using `role.getStateSupport().getInteractionIterationTarget()` for
the player - copying `ActionOpenBarterShop`'s own pattern exactly. But
`$Interaction` here only lasts **~1 second** before its own `Timeout`
calls `ReleaseTarget` and moves to `Watching` (that timing was fine for
the original barter shop, a synchronous open-on-click with no round
trip) - while the real LLM call that decides `OPEN_SHOP` routinely takes
longer than 1 second. By the time the reply lands and
`PendingShopOpen.request()` runs, the state machine has almost always
already left `$Interaction`, the interaction lock is already released,
and nothing is checking the flag anymore. The chat message still arrived
regardless, since `PlayerRef.sendMessage()` is a raw network send
independent of NPC state - which is exactly what made this confusing to
diagnose from the symptom alone (half the feature visibly worked).

**Fix**: moved the check into the `Watching` state instead (where a
conversation actually spends nearly all its time) and switched the
player-lookup from the transient interaction lock to the same
`InfoProvider.getPositionProvider().getTarget()` technique
`NoteNearbyThreatAction` already uses with a plain `"Player"` sensor -
no dependency on `$Interaction` or its lock's lifetime at all anymore.
Boot-tested clean; **not yet re-confirmed live with the actual UI opening
in front of a connected player** - that's the next thing to check.

## 🎉 2026-07-21: real barter shop, gated by taming - confirmed live, all three cases

Requested: the trader's real inventory/shop should open when talking to
him, up until he decides to become someone's companion (adventurer), at
which point he no longer trades.

**Real shop UI, not a fake one.** Disassembled the actual, shipped
`ActionOpenBarterShop.execute()` to find the exact mechanism:
`player.getPageManager().openCustomPage(ref, store, new
BarterPage(playerRef, shopId))`. `Merchant_Oskar` now opens the real
`Klops_Merchant` shop asset (`Server/BarterShops/Klops_Merchant.json` -
matches his own appearance) - an actually shipped, real inventory, not
placeholder data.

**A real thread-safety decision, not a guess.** Opening the shop needs a
`Store<EntityStore>` parameter - unlike `PlayerRef.sendMessage()`, which
needed none and was already proven safe to call from the async WebSocket
reply thread. Rather than assume `openCustomPage()` is equally safe
(guessing wrong here risks corrupting ECS state, a far worse failure than
a chat message not arriving), the async reply callback only ever touches
a new `PendingShopOpen.java` - a plain `ConcurrentHashMap` flag. The
actual `PageManager` call happens later, on the game tick thread, via a
new `OpenShopIfRequestedAction` ticking inside the `$Interaction` state -
the same thread every other Action in this plugin already safely runs
on. This required extending `NpcAiBridge`'s reply-callback signature from
a 2-arg `BiConsumer<String,String>` to a proper 3-arg `SayHandler`
(`npcId, text, action`) so the callback can see which action the LLM
decided, not just its spoken line.

**Gated on taming, confirmed live for all three cases**: added an
`is_tamed_by_anyone` context flag (distinct from `is_companion`, which is
per-player - this one means "became ANYONE's companion", since a tamed
trader stops trading for everyone, not just their new owner) enforced
both in the system prompt and, defense-in-depth, server-side in
`main.py` regardless of what the model outputs. Tested live end to end:
(1) a fresh trader asked about wares correctly says `ACTION: OPEN_SHOP`
(*"Of course, let me show you what I've got today"*); (2) directly taming
the same trader in the database (deterministic, rather than waiting on
the model's own mood to accept an offer) then (3) asking about wares
again **as a different player entirely** correctly gets `ACTION: NONE`
and *"I no longer run a shop. My days of trading are behind me"* - the
shop stays closed for everyone once a trader leaves, not just for
whoever tamed them.

## 2026-07-21: Adventurer archetype - real hostile detection, NPC-decided guide vs. fight

Requested: NPCs distributed across the world as traders or adventurers,
where adventurers are easier to befriend/tame and can either lead the
player to a nearby enemy or actually join the fight - the NPC's own
choice, not a fixed rule.

**Spawn distribution needs no new engineering.** NPCs spawn wherever the
player is standing when they run `/npc spawn <role>` - there's no way to
bake a fixed world position into a role JSON. "Trader here, adventurer
there" already works today: walk to different spots and spawn different
roles.

**Real hostile detection, confirmed via real shipped asset data, not a
guess.** New `Adventurer.json` role adds a `"Mob"` sensor with an
`"Attitude"`/`"Hostile"` prioritiser to its Watching state - the *exact*
JSON shape used by the real, shipped Trork combat AI
(`Component_Trork_Instruction_Panic.json`/`_Search.json` in the game's
own `Assets.zip`), not an invented API. A new `NoteNearbyThreat` action
reads the matched entity's live position via
`InfoProvider.getPositionProvider()` (confirmed via disassembly to expose
`getX/Y/Z()` alongside `getTarget()`) and records it in a new
`ThreatMemory.java` - a per-NPC cache with a 20-second staleness window,
since (unlike world geography) a hostile creature moves and can wander
off. This is read fresh on every conversation turn (unlike the static
`NearbyLandmarks` info, which is cached once per NPC forever) so a threat
that's left doesn't linger in the AI's awareness.

**Extended the NPC's decision vocabulary**: alongside the existing
`OFFER_GUIDE`/`DECLINE_GUIDE`/`ACCEPT_TAME`, added `OFFER_FIGHT` - the NPC
now genuinely chooses between leading the player to a threat (guide
only), actually fighting alongside them, or refusing entirely, weighing
its own aggression/courage and trust in the player. Added a new
`adventurer` personality baseline (bolder, higher starting trust -
"easier to convince to become a companion").

**A real personality-differentiation bug found and fixed by testing it
live**: the first test had both a bold adventurer *and* an untrusted, low
-aggression merchant choose `OFFER_FIGHT` for the identical prompt - the
personality trait wasn't actually steering the decision. Root cause: the
aggression trait's text description (`"avoids conflict"` for low values)
described social conflict-avoidance, not combat unwillingness, so the
model didn't connect it to "wouldn't fight a monster." Fixed by rewording
the trait descriptions to be explicitly combat-relevant (`"avoids danger
and physical confrontation, would rather not fight"`) and adding an
explicit rule connecting low aggression/trust to `OFFER_GUIDE`/
`DECLINE_GUIDE` rather than `OFFER_FIGHT`. Re-tested live: the same
prompt now gets a genuinely heroic `OFFER_FIGHT` from the adventurer
(*"Of course! We're in this together. Let's take it on!"*) and an
in-character decline from the merchant (*"I appreciate the offer, but
I'm not much for fighting."*).

**What's still deferred, same as before**: the plugin doesn't act on
`OFFER_FIGHT`/`OFFER_GUIDE` yet - no actual pathfinding-to-target or
combat AI has been wired up. This adds the *decision* layer (and the real
hostile-detection data feeding it) on top of what was already deferred;
the movement/combat *execution* engineering is unchanged in scope from
before. Also not yet live-tested with a real player and a real hostile
mob nearby (only simulated via direct wire-protocol messages, since that
part can't be exercised without a connected client standing near an
actual monster) - the hostile-detection sensor itself is boot-tested
(loads/validates with no errors) but not yet confirmed to fire against a
real creature in a real world.

## 2026-07-21: real name recognition + per-player memory - confirmed live, plus a real bug fixed

Requested: NPCs that recognize the player (can call them by name) and
actually use memory of past conversations (recall specific things, not
just generic acknowledgment). Found and fixed one real, serious bug along
the way, then confirmed the fix and the new features live against the
actual model - not just boot-tested.

**Real bug found: episodic memory recall wasn't scoped per player.**
`MemoryStore.recall_similar()` filtered only by `npc_id`, never
`player_id` - meaning if two different players talked to the same NPC,
their conversations could bleed into each other's recalled memories (the
NPC could "remember" something player B said while talking to player A).
Fixed to always filter by both. Verified live: had "Alice" tell an NPC a
made-up secret (a specific lucky number + a collectible), then had "Bob"
(a different player) ask the NPC about the same topics - confirmed Bob's
reply showed no knowledge of Alice's specifics, only generic reactions to
the words themselves.

**Added `recall_recent()`** - chronological (not similarity-based) recall
of a player's last few exchanges with an NPC, scoped the same way. Needed
so an NPC can bring up "last time we talked" even when the player's
current message (a plain "hi") doesn't semantically match the earlier
topic closely enough for similarity search alone to surface it. Merged
with `recall_similar()`'s results (recent-first, deduped) in
`main.py`'s `_build_context()`.

**Added real player name recognition.** The wire protocol now carries
`player_name` (from `PlayerRef.getUsername()` on the Java side - a real
in-game username, never used as a lookup key since names can change but
UUIDs can't) alongside `player_id`. `NPCContext.player_name` feeds the
system prompt so the NPC can address the player by name once it's spoken
with them a little.

**Confirmed live, both together**: asked a warm-personality test NPC
(innkeeper baseline) what it remembered about "Alice" after telling it a
made-up fact two turns earlier - it replied `"You said your lucky number
is 743 and you collect blue feathers. How unique!"` and, a turn later,
addressed her by name unprompted (`"...How are you feeling tonight,
Alice?"`) - both specific and unprompted, not generic acknowledgment.

**One real nuance worth knowing before you test this on your actual
NPCs**: the very first test used a deliberately aloof "blacksmith"
personality baseline, which gave vague, dismissive replies despite the
memory being correctly retrieved and present in its prompt (confirmed by
directly inspecting what was sent to the model) - that's in-character
standoffishness, not a memory bug. A reserved/gruff NPC (closer to
`Elder_Miri`'s low-warmth baseline than `Merchant_Oskar`'s warmer one)
may similarly downplay specifics even when it "knows" them - expect the
warmth trait to visibly affect how forthcoming an NPC is with recalled
details, not just its tone.

## 2026-07-21: location awareness + NPC-decided taming (map/movement/taming, part 1)

Requested: NPCs that know the map ("where's the nearest town"), can
temporarily guide a player somewhere depending on server load and their
own willingness, and can be tamed (max one per player, with the tamed NPC
getting more memory/resources). Built in two pieces, with a third
deliberately deferred - see below for why.

**Location awareness - implemented, NOT yet live-verified with a real
client.** Hytale has no "town" system (procedural terrain, not authored
settlements) - the real equivalent is a named world-gen `Zone`. Confirmed
via disassembly that Hytale ships a real "find nearest thing" mechanism
(`ChunkGenerator.getZonePatternProvider().getZones()` +
`SpiralSearchUtil.search()`, the same combo behind the built-in
`/locate zone` command), and confirmed via the actual shipped zone JSON
(`Server/World/Default/Zones/*/Zone.json`) that most zones carry a real
`"Discovery"` block with a proper in-fiction place name (e.g.
`"ZoneName": "Emerald_Wilds"` for the zone around spawn) distinct from
the internal id (`Zone1_Spawn`) - `NearbyLandmarks.java` uses that name,
not the raw id. Zone-at-a-coordinate is a pure procedural function backed
by an in-memory cache (confirmed via disassembly - no chunk loading/disk
I/O), so it's safe to call synchronously from the game thread; results
are cached per NPC (they're stationary, so the answer never changes).
Wired into the `situation` field already sent with every dialogue call -
no new wire-protocol message needed, the LLM just answers from context
it's already given. Compiles and boots clean; **needs a real player to
ask a spawned NPC where something is** to confirm it actually surfaces
real distances instead of coming back empty.

**NPC-decided taming - implemented AND verified live end to end**, not
just boot-tested. See below for the architecture and the real test
transcript (trust-building → genuine model decision → Postgres
enforcement, cross-checked against the database directly).

**Guiding movement - deliberately deferred.** Confirmed via disassembly
that real dynamic pathfinding exists (`AStarWithTarget.initComputePath()`
computes a path to an arbitrary runtime coordinate, `PathFollower` drives
movement along it), so it's genuinely buildable - but it's raw low-level
navigation plumbing, not a JSON-authorable action like everything else in
this plugin, and getting it right (multi-tick movement state, not
fighting the existing Idle/Watching/`$Interaction` state machine, walking
back to post afterward) is a meaningfully bigger lift than anything built
so far. The wire protocol is already forward-compatible for it - the
orchestrator's `"say"` reply already includes an `"action"` field with
`OFFER_GUIDE`/`DECLINE_GUIDE` values the NPC itself decides based on its
personality and role (a merchant won't wander from their post; see
`llm_client.py`'s `SYSTEM_TEMPLATE`) - the plugin just doesn't act on
that field yet.

## 2026-07-21: a cast, not one demo NPC - stable identity + two new characters

With the reply-delivery chain confirmed working end to end, the next step
was turning "one hardcoded test NPC" into an actual small cast. Two changes:

**Fixed a real latent bug: `npc_id` was the spawned entity's own UUID, not a
stable character identity.** Every `/npc spawn` (or world reload) creates a
brand-new random entity UUID, and `TalkToAIAction` was using
`UUIDComponent.getUuid()` as the `npc_id` sent to the orchestrator - so the
personality/trust/memory the orchestrator was building up per NPC was being
silently thrown away and restarted from scratch on every single respawn.
Fixed by keying `npc_id` on `role.getRoleName()` instead (e.g. `"AI_Talker"`,
`"Elder_Miri"`) - stable across respawns, so a character's relationship with
a player actually persists now. Trade-off, accepted deliberately: two
simultaneously-spawned entities of the *same* role would share one
identity/conversation slot - fine as long as each named character exists as
a single instance in the world, which is the intended setup.

**Added two optional JSON fields on the `TalkToAI` action** so a role can
customize its AI identity without touching Java: `"DisplayName"` (the chat
name/tag shown in `[Name] ...` replies - falls back to the role's own name)
and `"AIRole"` (an occupation word like `"elder"`/`"merchant"`, sent to the
orchestrator as `npc_role` - it feeds `DEFAULT_BASELINES` in
`orchestrator/main.py` for a starting personality, and the `{role}` slot in
the system prompt template; falls back to `"villager"`). Read via the
engine's own `getString()` config-helper (same one
`BuilderSensorCanInteract` uses for its `ViewSector` field) rather than raw
Gson, so the field is registered as "known" and doesn't trip the framework's
"Unknown JSON attribute" boot warning.

**Two new NPCs**, both verbatim copies of `AI_Talker.json`'s proven
Instructions/MotionControllerList block (only `Appearance`,
`MemoriesCategory`, and the two new `TalkToAI` fields differ) - appearance
names pulled straight from the shipped `Assets.zip` to guarantee they're
real, valid models:
- `Elder_Miri.json` - `Appearance: "Kweebec_Sapling_Treesinger"`,
  `AIRole: "elder"` (calm, low-aggression baseline in `DEFAULT_BASELINES`).
- `Merchant_Oskar.json` - `Appearance: "Klops_Merchant"` (a different
  creature model, not another Kweebec), `AIRole: "merchant"` (warmer,
  business-savvy baseline).

Compiles clean, boots clean, all three roles (`AI_Talker`, `Elder_Miri`,
`Merchant_Oskar`) load with no validation warnings or errors - **not yet
live-tested that Elder Miri and Oskar actually talk in-character with their
own personalities**, that's the next thing to confirm.

## 🎉 2026-07-20: it works, confirmed live, end to end

A real player spawned `AI_Talker` in a real Hytale world, clicked it, and
each click produced a real round trip visible in the orchestrator's own
logs: Qdrant memory recall → `llm-inference` chat completion → episodic
memory write, repeating on every click. **The AI is genuinely generating
in-character replies from a real in-game interaction.** The reply is now
also sent back as an actual chat message to the player
(`PlayerRef.sendMessage(Message.raw(...))`), not just logged. (This part
took one more day and two more real bugs to actually deliver the reply -
see the 2026-07-21 sections below; that's the version to treat as done.)

Getting here took three real, live-tested fixes, in order:
1. `PlayerInteractEvent` (the original approach) turned out to never fire
   for NPC clicks at all — NPC interactions run through a JSON-driven
   `Interaction`/`Action` system instead (confirmed by reading the shipped
   `Kweebec_Merchant` role JSON: `"Sensor": {"Type": "HasInteracted"}` →
   `"Actions": [{"Type": "OpenBarterShop", ...}]`, no event involved). Fix:
   registered a custom `TalkToAI` action type via
   `NPCPlugin.get().registerCoreComponentType(...)`, the same call the
   built-in barter-shop action uses.
2. The shipped `AI_Talker.json` role failed spawn validation
   (`"failed to find npc role: AI_Talker"` in-game) because
   `TalkToAIActionBuilder` redundantly called `readCommonConfig()` - the
   real, shipped `BuilderActionOpenBarterShop.readConfig()` doesn't call it,
   so neither should ours. Removing that call fixed it.
3. The NPC got stuck animating in place - a whole instruction (clearing the
   wave animation after a delay) had been dropped while simplifying the
   role JSON from the original `Kweebec_Merchant.json`. Restored verbatim.

**2026-07-21 postscript:** the "stuck running in place" symptom above came
back on a later spawn, on a *different* world (`NPC_TESTS`), even with the
verbatim-restored `Instructions` block confirmed byte-identical in the
installed jar - real regression suspected. Investigated via the actual
Hytale server/client logs (`UserData/Saves/<world>/logs/*.log`,
`UserData/Logs/*_client.log`), not just the local `runServer` boot test:
no error or warning anywhere tied to the `AI_Talker` entity, and the one
animation-related log line found (`No animation with id Spawn on entity
with model ... Kweebec_Rootling.blockymodel`) turned out to be harmless,
pre-existing noise present in every session log back to 2026-07-20,
including the confirmed-working ones - so not the cause. Spawning in a
flat creative world instead confirmed the NPC is fine. **Root cause:
terrain under the spawn point in that particular world, not the plugin,
the role JSON, or the jar.** No code fix needed; noting here since the
symptom is easy to mistake for the JSON regression above.

## 2026-07-21: reply-not-arriving bug found and fixed (stale `PlayerRef`)

After the chat listener above was confirmed to correctly intercept and cancel
the player's message (no stray broadcast, no console log line — expected,
since cancellation suppresses both), the AI's reply still never showed up in
the player's chat, even though `docker compose logs orchestrator --timestamps`
confirmed the backend was completing real LLM calls every time. Both
`TalkToAIAction.execute()` and `PlayerChatToAIListener.handle()` captured a
`PlayerRef` once (at click/chat time) and reused that same object ~1-2
seconds later inside the async reply callback, once the LLM round trip
finished. That reference appears to go stale over that gap — `PlayerRef` has
an `isValid()` method, which only makes sense if instances can become
invalid — so calling `sendMessage()` on it silently did nothing.

Fix: both callbacks now store only the player's `UUID` and re-resolve a
fresh `PlayerRef` at the moment the reply actually arrives, via
`Universe.get().getPlayer(playerUuid)` (`Universe` is a singleton reachable
the same way as `NPCPlugin.get()`; `getPlayer(UUID)` does a live lookup). If
that returns `null` (player disconnected mid-conversation) the reply is
logged and dropped instead of crashing. Compiles and boots clean; **still
needs a real player to confirm the reply now actually shows up in chat** —
that's the very next thing to test.

## 2026-07-21, part 2: the actual reason no reply ever arrived

The `PlayerRef` fix above was real, but re-testing live still produced
nothing: clicking the NPC drove up orchestrator CPU (real LLM calls
happening) but no chat message ever appeared. The actual root cause was in
`NpcAiBridge.handleMessage()`'s hand-rolled JSON reader: `extract()` searched
for the literal substring `"npc_id":"` with no space between the colon and
the opening quote. `orchestrator/main.py` builds its `"say"` reply with
Python's `json.dumps()`, which inserts a space after every colon by
default - `"npc_id": "abc"`, not `"npc_id":"abc"`. That single space meant
`extract()` returned `null` for every real reply the orchestrator ever sent,
so `handleMessage()` hit its `if (npcId == null) return;` guard and the
registered callback was **never once invoked**, regardless of anything else
being correct. This explains the exact symptom reported twice: the backend
genuinely working (visible in orchestrator logs / CPU) while nothing ever
reached the player.

Fixed by making `extract()` tolerate (and skip) whitespace between the key,
the colon, and the opening quote of the value, instead of assuming compact
zero-space JSON. Rebuilt, boots clean, `com.orffyrus:NpcAiStack` still loads
and enables with no plugin-specific errors - **this is the next thing to
re-test live**, and this time it should actually work end to end.

## 🎉 2026-07-21: confirmed working end to end, for real this time

A real player clicked `AI_Talker`, got a real AI reply visibly tagged with
the NPC's name in chat (`[AI_Talker] ...`), and continuing the conversation
by just typing in normal chat worked too. Both bugs above (stale
`PlayerRef`, the JSON whitespace parser bug) are confirmed actually fixed
live, not just "compiles and boots clean." This is the version to treat as
the known-good baseline - see the git tag `hytale-plugin-e2e-working` on
this commit.

Two environment gotchas cost real debugging time getting here and are easy
to hit again, so they're called out explicitly:

- **The player must be in Adventure mode, not Creative.** Hytale has only
  two gamemodes - `Adventure` and `Creative` (there is no "Survival";
  `/gamemode survival` silently fails as an invalid command name and
  leaves you in whatever mode you were already in). The NPC interact
  prompt does not appear at all in Creative mode - `/gamemode adventure`
  before testing.
- **Every new world defaults to `NpcAiStack` disabled**, per Hytale's
  per-world mod settings - confirmed via
  `[PluginManager] Skipping mod com.orffyrus:NpcAiStack (Disabled by
  server config)` in that world's own server log
  (`UserData/Saves/<world>/logs/*.log`). This produces a
  `"failed to find npc role: AI_Talker"` error that looks like a spawn
  bug but isn't - enable the mod in that world's Mods settings first.

## Multi-turn conversation via chat

Clicking `AI_Talker` now also starts a tracked "conversation" for that
player (`NpcAiPlugin.ACTIVE_CONVERSATIONS`, keyed by `PlayerRef` UUID).
While a conversation is active, anything that player types in normal chat
gets intercepted by `PlayerChatToAIListener` (registered via
`getEventRegistry().registerAsyncGlobal(PlayerChatEvent.class, ...)` -
`PlayerChatEvent` is `IAsyncEvent`, a different registration shape than the
`Consumer`-based `registerGlobal` used elsewhere), cancelled so it never
hits normal server chat (`event.setCancelled(true)`), and forwarded as the
real typed text to the AI instead of the canned "the player interacts with
you" line used on first click. Replies are prefixed with the NPC's role
name (`Role.getRoleName()`, e.g. `[AI_Talker] ...`) for visual
identification. Saying "bye"/"goodbye"/"exit"/"leave"/"stop" ends the
conversation locally without an LLM call; conversations also expire after
5 minutes of inactivity. **Confirmed live 2026-07-21** - see above.

The cancel-suppresses-broadcast contract was confirmed by disassembling
`GamePacketHandler` (which dispatches `PlayerChatEvent` and checks
`isCancelled()` before broadcasting to `event.getTargets()`) - but there
was no existing shipped example of a *listener* for this event to copy
from (unlike `TalkToAI`, where `ActionOpenBarterShop` was a perfect
reference).

`PlayerInteractEvent` is still registered in `NpcAiPlugin` but is **not**
the NPC-talk mechanism - kept only in case it's useful for something else.

## What's real vs. what's still unverified

| Piece | Status |
|---|---|
| `manifest.json`, plugin lifecycle, `EventRegistry.registerGlobal` | **Confirmed** — real server boot |
| `PlayerInteractEvent` fires for NPC clicks | **Confirmed false** — not the mechanism, see above |
| `NPCPlugin.get().registerCoreComponentType("TalkToAI", ...)` | **Confirmed** — action fires for real |
| Custom asset pack (`Adventurer.json`) ships, loads, and spawns | **Confirmed live** (as of the 4-NPC era's `AI_Talker`/shared code path - `Elder_Miri`/`Merchant_Oskar`/`AI_Talker` deleted 2026-07-22, project is Adventurer-only now, see CLAUDE.md) |
| Stable per-character `npc_id` (role name, not spawned entity UUID) | Fixed 2026-07-21, **not yet live-verified that trust/memory actually survives a respawn** - the bug it fixes (identity resetting every respawn) was real but silent, so there's no in-game symptom to re-check other than confirming the fix doesn't regress anything |
| Clicking `AI_Talker` triggers a real dialogue request | **Confirmed live** — repeated orchestrator round trips (Qdrant recall → LLM call → memory write) on each click |
| Reply reaches the player as a chat message | **Confirmed live** — two real bugs found and fixed along the way: stale captured `PlayerRef` (fixed via `Universe.get().getPlayer(uuid)`) and `NpcAiBridge.extract()` requiring zero-space JSON when Python's `json.dumps()` emits a space (fixed to tolerate whitespace) |
| Player lookup in `TalkToAIAction.execute()` | **Confirmed live** — the whole chain depends on it and it works |
| Thread-hop before `sendMessage()` in the reply callback | Reasoned-not-verified: `PlayerRef.sendMessage()` is used elsewhere from async command handlers in this codebase, so it's likely cross-thread-safe, but this is inference, not a confirmed guarantee. No entity/world-state mutation happens in the callback (kept deliberately narrow because of this). Not observed to be an issue across live testing so far. |
| Multi-turn conversation (`PlayerChatEvent`) | **Confirmed live** — typing after the first click continues the conversation with real text, visually tagged replies |
| `NoteAttackedByPlayer` action + `"Damage"` sensor (`Combat: true`) reports `player_attacked_npc` to the orchestrator | Real, shipped Sensor Type - confirmed via disassembly of `SensorDamage`/`BuilderSensorDamage` AND via its real usage in `Server/NPC/Roles/_Core/Components/Steps/Component_Instruction_Damage_Check.json` (the neutral Kweebec's own Panic trigger), added to all 4 role JSONs 2026-07-21. **Boot-tested clean** (`./gradlew runServer` validates and boots with zero errors attributable to it) - **not yet confirmed against a real attack**. All 4 roles set `Invulnerable: true`; grounded in `DamageSystems$PlayerDamageFilterSystem`'s own pattern (cancels damage via a flag on an event that still fires, rather than suppressing the event outright) that the Damage sensor should still match regardless, but that's inference from disassembly, not a live hit. To verify: spawn any of the 4 roles, attack it, and check the orchestrator log for `npc <id> (player <id>): outcome=player_attacked_npc` (added 2026-07-21 - `handle_outcome()` previously only logged its failure paths, so a successfully recorded outcome was invisible in the log) plus a new row in Postgres `npc_outcome_log`. |
| Reactive companion defense - `"EntityEvent"` sensor (`EventType: Damage`, `SearchType: PlayerOnly`) makes a tamed Adventurer companion react to the PLAYER being hit, not just a hostile it can already see | Real, shipped Sensor Type - confirmed via disassembly of `SensorEntityEvent`/`BuilderSensorEntityEvent` AND via its real usage in `Component_Instruction_Allied_Damage_Check.json`, added 2026-07-21 (requested: "npc must fight with me if I am attacked"). **Boot-tested clean** - **not yet live-confirmed**, same caveat as the companion-combat system it extends (see above: no live session has happened near a real hostile yet). |
| "Thinking" particle over the NPC's head while a reply is in flight (`IsAwaitingReply` sensor + `Question_Subtle.particlesystem`) | Real, shipped particle asset and `SpawnParticles` action (already used by `NoteNearbyThreat`'s sibling work), added 2026-07-21 (requested: an "AI process message waiting" icon, since replies arriving instantly breaks immersion). **Live-tested same day - genuinely never appeared.** Root cause: relied on the JSON `"Once": true` modifier to throttle re-spawning, but disassembly of `SensorBase` plus a real reference (`Component_Kweebec_Instruction_Search.json`) showed every real, shipped use of `"Once"` pairs it with an unconditional `"Any"` sensor to mean "once per state entry" - not "once per rising edge of a condition," which is what `IsAwaitingReply` needed (it can flip true/false several times within one uninterrupted `Watching` visit). The offset/positioning (`[0, 2.1, 0]`) was independently confirmed correct via that same reference file, so that wasn't the issue. Fixed: `IsAwaitingReplySensor` now self-throttles with its own 2s-per-NPC cooldown, independent of the framework's state-entry-tied `Once` lifecycle. `Once` removed from all 8 occurrences. **Boot-tested clean again - not yet re-confirmed live** (needs the next play session). The companion piece of this - the minimum 1.3s reply delay in `main.py` - **is** confirmed live: three real dialogue turns through a real WebSocket connection each measured exactly 1.30s. |

## How to actually test this (needs a real client - can't be done from here)

1. `docker compose up -d --build` at the repo root.
2. Build + install:
   ```bash
   ./gradlew build
   cp build/libs/NpcAiStack-0.1.0.jar "$APPDATA/Hytale/UserData/Mods/"
   ```
3. In-game: enable `NpcAiStack` in your world's mod settings (**every new
   world defaults to it disabled** - this tripped up testing once already;
   check `[PluginManager] Skipping mod com.orffyrus:NpcAiStack (Disabled by
   server config)` in the log if spawning fails with "failed to find npc
   role").
4. `/op self` if you haven't already, and `/gamemode adventure` - the NPC
   interact prompt does not appear in Creative mode at all.
5. `/npc clean` then `/npc spawn Adventurer` (the project's single NPC as
   of 2026-07-22 - `Elder_Miri`/`Merchant_Oskar`/`AI_Talker` were deleted,
   see CLAUDE.md's consolidation entry).
6. Interact with it (click) - a reply should now appear as a chat message
   tagged `[Adventurer] ...`, and typing afterward in normal chat should
   continue the conversation. Watching `docker compose logs orchestrator -f`
   at the same time confirms the backend side regardless of what shows up
   in-game.

## A real bug found and fixed by actually running this (still applies)

The official template's own `runServerJar` Gradle task passes the shadow
jar's file path directly as a `--mods` argument. On this server build that
throws `ValueConversionException: Path must be a directory!` — `--mods`
entries have to be directories. Separately, the server *always* auto-scans a
working-directory-relative `mods/` folder even with no `--mods` flag at all,
so also passing `--mods=<that same dir>` explicitly causes
`Tried to load duplicate plugin`. `build.gradle` here copies the shadow jar
into `run/mods/` and only passes `--mods` when `load_user_mods` pulls in a
*different* directory (the real `UserData/Mods`) — see the comments in the
`runServerJar` task.

Also caught: the shadow jar's `include` filter needs `Server/**` in
addition to `com/orffyrus/**` and `manifest.json`, or the shipped asset
pack (`AI_Talker.json`) silently gets left out of the built jar entirely.

## Build

```bash
./gradlew build
```

Needs a JDK 25 (this repo was verified with Eclipse Temurin 25.0.3) - see
the [official template](https://github.com/realBritakee/hytale-template-plugin)
this was adapted from for full IDE setup (IntelliJ IDEA recommended).

`gradle.properties` already points `hytale_home` auto-detection at the
standard install path, and `hytale_build=0.5.7` matches this machine's
installed server as of 2026-07-20 - update it if you're on a different
build (a mismatch fails the Maven dependency resolution loudly, not
silently). `includes_pack=true` since this now ships `AI_Talker.json`.

## Run a local test server (no real Hytale client attached yet)

```bash
./gradlew runServer
```

Builds, copies the jar into `run/mods/`, and launches `HytaleServer.jar`
with `--allow-op --disable-sentry`. Confirms the plugin/asset pack load and
the server boots - it can NOT confirm the actual spawn/interact/dialogue
flow, since that needs a connected player (`/npc spawn` requires
`AbstractPlayerCommand` context, which the console alone can't provide).
Ctrl+C to stop it; `run/` is gitignored.

## Next steps, in order

1. ~~Get a JDK 25 + this compiling.~~ **Done.**
2. ~~Confirm the plugin actually loads in a real server.~~ **Done.**
3. ~~Find the real NPC-interaction mechanism.~~ **Done** - `TalkToAI` action
   + `AI_Talker` role, live-confirmed working end to end.
4. ~~Confirm a real click reaches the orchestrator.~~ **Done, live** -
   repeated real dialogue round trips confirmed in the orchestrator's logs.
5. ~~Wire `PlayerChatEvent` for real multi-turn conversation.~~ **Done,
   confirmed live.**
6. ~~Confirm live: does the reply show up as a chat message?~~ **Done,
   confirmed live** - two real bugs found and fixed along the way (stale
   `PlayerRef`, JSON whitespace parsing), see above.
7. ~~Confirm live: does typing after the first click continue the
   conversation with real text?~~ **Done, confirmed live 2026-07-21.**
   Tagged `hytale-plugin-e2e-working` as the known-good baseline.
8. Confirm the thread-hop question properly (or find a case where it
   actually matters - neither reply callback touches entity/world state
   today, only sends a chat message; no issue observed in live testing
   so far, but this is inference, not a confirmed guarantee).
9. ~~Give the world an actual cast instead of one demo NPC.~~ **Done,
   compiles and boots clean** - fixed `npc_id` to be stable per character
   (was silently the spawned entity's UUID, resetting memory/trust on
   every respawn), added `DisplayName`/`AIRole` config to `TalkToAI`, and
   shipped two new characters (`Elder_Miri`, `Merchant_Oskar`) with their
   own personality baselines.
10. **You are here** - spawn `Elder_Miri` and `Merchant_Oskar` live and
    confirm each actually talks in character (distinct tone/personality
    from `AI_Talker`), and that talking to the same character again after
    a respawn/relog remembers the relationship instead of starting fresh.
11. Sustained/repeat testing: does the conversation survive multiple
    NPCs at once, a player disconnecting mid-conversation (the
    `Universe.getPlayer()` null-check path), or the 5-minute conversation
    timeout actually firing?
12. `skill_writer.py` against the real GPU/model (only verified so far
    with a fake LLM/DB) - see the root `CLAUDE.md` "Agreed next steps".
13. Load-test llama.cpp slots (`--parallel`/ctx tradeoff) with multiple
    concurrent NPC conversations for real, not just the fake-player test
    client.
14. ~~NPC location awareness + NPC-decided taming.~~ **Taming done,
    confirmed live end to end.** Location awareness implemented and
    boot-tested, **not yet confirmed with a real client** - ask a spawned
    NPC where something is and see if it names a real place/direction/
    distance instead of staying vague.
15. Guiding movement (deliberately deferred - see "part 1" section above
    for why). The wire protocol already carries `OFFER_GUIDE`/
    `DECLINE_GUIDE` decisions from the NPC; needs `AStarWithTarget`/
    `PathFollower` wired into a new NPC action, careful multi-tick state
    handling, and a "walk back to post afterward" behavior.
