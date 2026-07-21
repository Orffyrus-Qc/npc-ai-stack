package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.server.core.universe.PlayerRef;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.ActionBase;
import com.hypixel.hytale.server.npc.role.Role;
import com.hypixel.hytale.server.npc.sensorinfo.IPositionProvider;
import com.hypixel.hytale.server.npc.sensorinfo.InfoProvider;

import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Runtime "NoteAttackedByPlayer" action. Fires whenever the preceding
 * Sensor - real, shipped "Damage" sensor Type ({@code Combat: true}),
 * confirmed via disassembly of SensorDamage/BuilderSensorDamage AND via its
 * real, shipped usage in Server/NPC/Roles/_Core/Components/Steps/
 * Component_Instruction_Damage_Check.json (referenced by the neutral
 * Kweebec's own Panic behavior) - reports this NPC took combat damage.
 *
 * Unlike NoteNearbyThreat (a hostile MOB nearby - flavor text only), this
 * reports a real gameplay OUTCOME to the orchestrator
 * (personality.py's OUTCOME_EFFECTS["player_attacked_npc"]) via
 * NpcAiBridge.sendOutcome() - see that method's javadoc for why npc_role
 * is required. Player-only: the Damage sensor's target could be any
 * entity (a hostile mob's stray hit, environmental damage misrouted
 * through Combat, etc.) - only counted as an outcome if it resolves to a
 * real PlayerRef.
 *
 * Debounced per NPC (COOLDOWN_MILLIS) - same reasoning as ThreatMemory's
 * staleness window: a single real attack is several hits in a few seconds
 * (this Action's Sensor can match once per tick), and personality.py's
 * LEARNING_RATE assumes "one nudge per interaction event", not "one nudge
 * per hit" - without this, a single combat encounter could swing trust far
 * more than a single outcome ever should.
 */
public class NoteAttackedByPlayerAction extends ActionBase {

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();
    private static final long COOLDOWN_MILLIS = 15_000L;
    private static final ConcurrentHashMap<String, Long> LAST_SENT = new ConcurrentHashMap<>();

    private final String aiRole;

    public NoteAttackedByPlayerAction(NoteAttackedByPlayerActionBuilder builder, BuilderSupport support) {
        super(builder);
        this.aiRole = builder.aiRole;
    }

    @Override
    public boolean execute(Ref<EntityStore> ref, Role role, InfoProvider info,
                            double delta, Store<EntityStore> store) {
        super.execute(ref, role, info, delta, store);

        IPositionProvider attacker = info.getPositionProvider();
        if (attacker == null || !attacker.hasPosition()) {
            return false;
        }
        Ref<EntityStore> attackerRef = attacker.getTarget();
        if (attackerRef == null) {
            return false;
        }
        PlayerRef playerRef = store.getComponent(attackerRef, PlayerRef.getComponentType());
        if (playerRef == null) {
            // Damage came from something other than a player (a hostile
            // mob's own hit, etc.) - not an outcome about a PLAYER's
            // behavior, so nothing to report.
            return false;
        }

        String npcId = role.getRoleName();
        long now = System.currentTimeMillis();
        Long last = LAST_SENT.get(npcId);
        if (last != null && now - last < COOLDOWN_MILLIS) {
            return true;
        }
        LAST_SENT.put(npcId, now);

        UUID playerUuid = playerRef.getUuid();
        LOGGER.atInfo().log(npcId + " was attacked by player " + playerUuid);

        NpcAiBridge bridge = NpcAiPlugin.BRIDGE;
        if (bridge != null) {
            bridge.sendOutcome(npcId, aiRole, playerUuid.toString(), "player_attacked_npc");
        }
        return true;
    }
}
