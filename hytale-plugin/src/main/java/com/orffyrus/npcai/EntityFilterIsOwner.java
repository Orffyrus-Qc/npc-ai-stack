package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.server.core.universe.PlayerRef;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.EntityFilterBase;
import com.hypixel.hytale.server.npc.role.Role;

import java.util.UUID;

/**
 * Runtime "IsOwner" entity filter - true if the CANDIDATE is the real
 * PlayerRef that owns this NPC as a companion (CompanionState.getOwner()),
 * false for any other player (or non-player entity).
 *
 * 2026-07-22: added alongside CompanionState's new per-NPC owner tracking to
 * fix the "follows the nearest player, not specifically its owner" v1
 * simplification (see CompanionState.java's javadoc). Paired with the
 * built-in "Player" sensor (confirmed via disassembly that
 * BuilderSensorPlayer extends BuilderSensorEntityBase, the same
 * Filters-supporting base Mob uses - see EntityFilterHostileSpecies.java for
 * the same pattern) on Adventurer.json's companion-follow blocks, so a
 * companion now seeks specifically the player who tamed it, not whichever
 * player happens to be nearest.
 *
 * Deliberately NOT applied to the reactive-defense "EntityEvent" sensor
 * (Adventurer.json's widened-search block): confirmed via disassembly that
 * BuilderSensorEntityEvent extends BuilderSensorEvent, not
 * BuilderSensorWithEntityFilters - it has no candidate-iteration/Filters
 * mechanism at all (it's a slot/subscription check for "did this event type
 * happen nearby", not an entity search), so there is no candidate Ref to
 * filter by owner there. Worst case left by this gap: the companion widens
 * its hostile-search range when ANY nearby player is hit, not just its
 * owner - the actual lock/chase/attack target is still gated by
 * IsHostileSpecies regardless, so this doesn't cause a wrong-target attack,
 * just an occasional unnecessary extra search.
 */
public class EntityFilterIsOwner extends EntityFilterBase {

    public EntityFilterIsOwner(EntityFilterIsOwnerBuilder builder, BuilderSupport support) {
    }

    @Override
    public boolean matchesEntity(Ref<EntityStore> self, Ref<EntityStore> candidate, Role role, Store<EntityStore> store) {
        UUID owner = CompanionState.getOwner(role.getRoleName());
        if (owner == null) {
            return false;
        }
        PlayerRef playerRef = store.getComponent(candidate, PlayerRef.getComponentType());
        if (playerRef == null) {
            return false;
        }
        return owner.equals(playerRef.getUuid());
    }

    @Override
    public int cost() {
        return MINIMAL_COST;
    }
}
