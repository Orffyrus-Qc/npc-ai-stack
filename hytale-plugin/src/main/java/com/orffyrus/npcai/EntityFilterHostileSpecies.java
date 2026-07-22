package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.server.core.asset.type.attitude.Attitude;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.EntityFilterBase;
import com.hypixel.hytale.server.npc.entities.NPCEntity;
import com.hypixel.hytale.server.npc.role.Role;

/**
 * Runtime "IsHostileSpecies" entity filter - true if the CANDIDATE's own
 * role declares {@code DefaultPlayerAttitude: Hostile}, i.e. it is a
 * species that attacks players in vanilla gameplay (Trork, Goblin,
 * Outlander, Scarak, ...).
 *
 * 2026-07-21: added to fix "npc must be hostile to all enemy". The
 * existing Mob+Attitude sensor (see Adventurer.json) filters candidates by
 * a PAIRWISE relationship - WorldSupport.getAttitude(candidate, self) -
 * confirmed via disassembly of AttitudeView.getAttitude() to resolve in
 * this order: (1) any per-entity attitude override, then (2) an
 * AttitudeGroup lookup (each species' own "Groups" JSON lists which OTHER
 * group names it holds Hostile/Neutral/etc. toward - Trork.json lists
 * "Kweebec", but Goblin.json's own Hostile list is empty, Outlander.json
 * has no Hostile key, Scarak.json is hostile only to "Feran" - fetched and
 * confirmed live from the shipped Server/NPC/Attitude/Roles assets), then
 * finally (3) the OBSERVER's (self's) own DefaultNPCAttitude as a last
 * resort. Since Adventurer's AttitudeGroup is "Kweebec" and only Trork's
 * group actually names Kweebec, every other hostile species falls through
 * to step 3 and resolves to Adventurer's own DefaultNPCAttitude
 * ("Ignore") - the pairwise system was never going to generalize to
 * "every hostile species", because it encodes narrow ecological rivalries
 * (Trork vs Kweebec, Scarak vs Feran), not "is this thing dangerous".
 *
 * DefaultPlayerAttitude is the one flag every dangerous monster sets
 * consistently (it is literally what makes vanilla mobs attack players),
 * so this filter reads it directly off the CANDIDATE's own Role/
 * WorldSupport instead of going through the pairwise resolver at all.
 * Combined in Adventurer.json with a widened AttitudesByPriority (now
 * includes "Ignore") so the Attitude Prioritiser's own auto-generated
 * inclusion filter (ISensorEntityPrioritiser.buildProvidedFilters(),
 * confirmed via disassembly of BuilderSensorWithEntityFilters.getFilters())
 * never rejects a candidate this filter already matched - this filter is
 * now the real gate, the Prioritiser only ranks among survivors.
 */
public class EntityFilterHostileSpecies extends EntityFilterBase {

    public EntityFilterHostileSpecies(EntityFilterHostileSpeciesBuilder builder, BuilderSupport support) {
    }

    @Override
    public boolean matchesEntity(Ref<EntityStore> self, Ref<EntityStore> candidate, Role role, Store<EntityStore> store) {
        NPCEntity npc = store.getComponent(candidate, NPCEntity.getComponentType());
        if (npc == null) {
            return false;
        }
        Role candidateRole = npc.getRole();
        if (candidateRole == null) {
            return false;
        }
        return candidateRole.getWorldSupport().getDefaultPlayerAttitude() == Attitude.HOSTILE;
    }

    @Override
    public int cost() {
        return MINIMAL_COST;
    }
}
