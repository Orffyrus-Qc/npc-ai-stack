package com.orffyrus.npcai;

import com.hypixel.hytale.builtin.adventure.shop.barter.BarterPage;
import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.server.core.entity.entities.Player;
import com.hypixel.hytale.server.core.universe.PlayerRef;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.ActionBase;
import com.hypixel.hytale.server.npc.role.Role;
import com.hypixel.hytale.server.npc.sensorinfo.InfoProvider;

/**
 * Runtime "OpenShopIfRequested" action. Ticks (via a "Continue: true" node
 * in the $Interaction state, alongside the existing HeadMotion:Watch) while
 * a player is talking to this NPC, checking PendingShopOpen for a queued
 * request and - if one exists for the currently interacting player - opens
 * the real barter shop UI exactly the way the real, shipped
 * ActionOpenBarterShop.execute() does (disassembled from HytaleServer.jar):
 * resolve the interaction target -> PlayerRef/Player components ->
 * Player.getPageManager().openCustomPage(ref, store, new BarterPage(...)).
 *
 * This runs on the game tick thread (same as every other Action here), so
 * unlike the async WebSocket reply callback, it's safe to touch the Store-
 * taking PageManager API directly - see PendingShopOpen's javadoc for why
 * that call isn't made straight from the callback instead.
 */
public class OpenShopIfRequestedAction extends ActionBase {

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();

    private final String shopId;

    public OpenShopIfRequestedAction(OpenShopIfRequestedActionBuilder builder, BuilderSupport support) {
        super(builder);
        this.shopId = builder.shopId;
    }

    @Override
    public boolean execute(Ref<EntityStore> ref, Role role, InfoProvider info,
                            double delta, Store<EntityStore> store) {
        super.execute(ref, role, info, delta, store);

        if (shopId.isEmpty()) {
            return false;
        }

        Ref<EntityStore> targetRef = role.getStateSupport().getInteractionIterationTarget();
        if (targetRef == null) {
            return false;
        }
        PlayerRef playerRef = store.getComponent(targetRef, PlayerRef.getComponentType());
        if (playerRef == null) {
            return false;
        }
        if (!PendingShopOpen.consumeIfRequested(playerRef.getUuid())) {
            return false;
        }

        Player player = store.getComponent(targetRef, Player.getComponentType());
        if (player == null) {
            return false;
        }

        player.getPageManager().openCustomPage(targetRef, store, new BarterPage(playerRef, shopId));
        LOGGER.atInfo().log("Opened shop '" + shopId + "' for " + playerRef.getUsername());
        return true;
    }
}
