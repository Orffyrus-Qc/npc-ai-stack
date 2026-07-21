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
import com.hypixel.hytale.server.npc.sensorinfo.IPositionProvider;
import com.hypixel.hytale.server.npc.sensorinfo.InfoProvider;

/**
 * Runtime "OpenShopIfRequested" action. Ticks (via a "Continue: true" node
 * alongside the existing "Player"+HeadMotion:Watch sensor in the Watching
 * state - see NOTE below on why not $Interaction) while a player is nearby,
 * checking PendingShopOpen for a queued request and - if one exists for the
 * detected player - opens the real barter shop UI exactly the way the real,
 * shipped ActionOpenBarterShop.execute() does (disassembled from
 * HytaleServer.jar): PlayerRef/Player components ->
 * Player.getPageManager().openCustomPage(ref, store, new BarterPage(...)).
 *
 * NOTE (real bug found live): this originally lived in the $Interaction
 * state and used role.getStateSupport().getInteractionIterationTarget() for
 * the player, matching ActionOpenBarterShop's own pattern. That target is
 * only valid while $Interaction is active - but $Interaction here only lasts
 * ~1s (see AI_Talker/Merchant_Oskar.json's "Return to Watching" Timeout)
 * before ReleaseTarget clears the lock and the state machine moves to
 * Watching, while the real LLM round trip (where OPEN_SHOP gets decided)
 * routinely takes longer than that. The chat message still arrived (a raw
 * PlayerRef.sendMessage(), independent of NPC state), but the shop-open flag
 * was never read again once out of $Interaction, so it silently never
 * fired. Fixed by using the same nearby-player detection NoteNearbyThreatAction
 * already uses (InfoProvider.getPositionProvider().getTarget(), populated by
 * a plain "Player" sensor rather than the transient interaction lock) and
 * moving this into the Watching state, which is where a conversation
 * actually spends most of its time.
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

        IPositionProvider posProvider = info.getPositionProvider();
        if (posProvider == null || !posProvider.hasPosition()) {
            return false;
        }
        Ref<EntityStore> targetRef = posProvider.getTarget();
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
