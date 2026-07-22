package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.SensorBase;
import com.hypixel.hytale.server.npc.role.Role;
import com.hypixel.hytale.server.npc.sensorinfo.InfoProvider;

import java.util.concurrent.ConcurrentHashMap;

/**
 * Runtime "IsAwaitingReply" sensor - true while AwaitingReplyState has this
 * NPC's role name marked (see that class's javadoc), throttled to re-match
 * at most once per RESPAWN_COOLDOWN_MILLIS so the "thinking" particle
 * re-fires periodically for a long wait instead of spamming every tick.
 *
 * Does NOT use the JSON "Once" sensor modifier for this, despite that
 * looking like the obvious fit at first - confirmed via disassembly of
 * SensorBase (matches() returns true unless both "once" and "triggered"
 * are set; "triggered" is only flipped by setOnce()/clearOnce(), which
 * this sensor never calls itself)
 * and via the framework's own real usage: every real, shipped occurrence of
 * "Once": true (Component_Kweebec_Instruction_Search.json's own
 * "Question"-particle SpawnParticles being the closest analog to this exact
 * feature, and Component_Instruction_Wild_Panic_Passive.json) pairs it ONLY
 * with an unconditional "Type": "Any" sensor, inside a state-specific
 * Instructions branch - i.e. "Once" means "once per STATE ENTRY", set/reset
 * by the state transition machinery, not "once per rising edge of a
 * dynamic condition while remaining in the same state". IsAwaitingReply can
 * flip true/false/true many times during one uninterrupted "Watching"
 * visit (one per chat message), so "Once" would latch after the first
 * reply and never fire again for the rest of the conversation - confirmed
 * live: the icon never appeared. This cooldown is self-contained in Java
 * instead, independent of that state-entry lifecycle.
 */
public class IsAwaitingReplySensor extends SensorBase {

    private static final long RESPAWN_COOLDOWN_MILLIS = 2_000L;
    private static final ConcurrentHashMap<String, Long> LAST_MATCHED = new ConcurrentHashMap<>();

    public IsAwaitingReplySensor(IsAwaitingReplySensorBuilder builder, BuilderSupport support) {
        super(builder);
    }

    @Override
    public boolean matches(Ref<EntityStore> ref, Role role, double delta, Store<EntityStore> store) {
        if (!super.matches(ref, role, delta, store)) {
            return false;
        }
        String npcId = role.getRoleName();
        if (!AwaitingReplyState.isAwaiting(npcId)) {
            return false;
        }
        long now = System.currentTimeMillis();
        Long last = LAST_MATCHED.get(npcId);
        if (last != null && now - last < RESPAWN_COOLDOWN_MILLIS) {
            return false;
        }
        LAST_MATCHED.put(npcId, now);
        return true;
    }

    @Override
    public InfoProvider getSensorInfo() {
        return null;
    }
}
