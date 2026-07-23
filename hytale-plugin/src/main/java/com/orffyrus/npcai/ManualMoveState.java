package com.orffyrus.npcai;

import java.util.concurrent.ConcurrentHashMap;

/**
 * Per-NPC "the player just told me to walk forward / jump" pending
 * directive - an escape hatch for the real, live-confirmed "companion gets
 * logically stuck" symptom: SeekLandmarkSensor's own diagnostic logging
 * (2026-07-22) showed real guide/follow distance frozen at the exact same
 * value across many ticks before giving up - the real A* pathfinder behind
 * "Seek" (BodyMotionFind, confirmed via disassembly of
 * BodyMotionFindBase - a genuine navmesh/A* system with its own
 * onBlockedPath()/onNoPathFound()/NavState.BLOCKED, not a naive straight-
 * line walk) can decide it has no usable route even when a short manual
 * nudge would free it (a doorway/gap it's conservative about, a corner
 * pocket, etc).
 *
 * There is no "Jump" NPC action/body-motion type anywhere in the real
 * HytaleServer.jar (confirmed by disassembling NPCPlugin's full
 * registerCoreComponentType("Nothing"/"Wander"/.../"Seek"/"Flee"/
 * "Teleport"/... ) call chain - no such entry exists), so this is
 * implemented as a short, forward-biased BodyMotionTeleport nudge instead
 * of a real jump - see Mori.json/Pest.json's new ManualMove Instructions
 * nodes and BuilderBodyMotionTeleport's real MaxYOffset field (confirmed
 * via disassembly, same mechanism already proven live by the existing
 * follow-teleport-catchup feature).
 *
 * TTL-swept the same shape as AwaitingReplyState/ThreatMemory - a directive
 * that never gets a chance to fire (NPC not in Watching state, e.g. mid-
 * combat or mid-$Interaction) shouldn't linger and fire unexpectedly much
 * later.
 */
public final class ManualMoveState {

    public enum Kind { FORWARD, JUMP, FORWARD_JUMP }

    private static final long TTL_MILLIS = 5_000L;

    private record Pending(Kind kind, long requestedAtMillis) { }

    private static final ConcurrentHashMap<String, Pending> PENDING = new ConcurrentHashMap<>();

    static {
        new java.util.Timer(true).scheduleAtFixedRate(new java.util.TimerTask() {
            public void run() {
                long now = System.currentTimeMillis();
                PENDING.entrySet().removeIf(e -> now - e.getValue().requestedAtMillis() > TTL_MILLIS);
            }
        }, TTL_MILLIS, TTL_MILLIS);
    }

    private ManualMoveState() { }

    public static void request(String npcId, Kind kind) {
        PENDING.put(npcId, new Pending(kind, System.currentTimeMillis()));
    }

    /** True if a directive of exactly this kind is pending and unexpired -
     * does NOT consume it (side-effect-free peek, safe to call from
     * multiple sensor instances/ticks before one of them actually fires). */
    public static boolean isPending(String npcId, Kind kind) {
        Pending p = PENDING.get(npcId);
        return p != null && p.kind() == kind
                && System.currentTimeMillis() - p.requestedAtMillis() <= TTL_MILLIS;
    }

    /** One-shot consume, called by IsManualMoveSensor the instant it
     * actually matches (same tick) - guarantees the Teleport fires exactly
     * once per request, independent of the JSON "Once" modifier's real
     * "once per state entry" semantics (confirmed the wrong tool for this
     * exact shape of problem once already - see IsAwaitingReplySensor's
     * own javadoc). */
    public static void consume(String npcId) {
        PENDING.remove(npcId);
    }
}
