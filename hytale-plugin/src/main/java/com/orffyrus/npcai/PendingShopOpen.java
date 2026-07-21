package com.orffyrus.npcai;

import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Bridges the AI's "OPEN_SHOP" decision (arriving async, on the WebSocket
 * thread, from TalkToAIAction/PlayerChatToAIListener's reply callback) to
 * the actual shop-opening call (Player.getPageManager().openCustomPage(),
 * confirmed via disassembly to take a Store<EntityStore> parameter - unlike
 * PlayerRef.sendMessage(), which needs no Store and was separately confirmed
 * safe to call cross-thread).
 *
 * Rather than guess whether openCustomPage() is safe to call from a foreign
 * thread (a wrong guess here risks corrupting ECS state, a much worse
 * failure mode than a chat message silently not arriving), this class is
 * just a plain thread-safe flag: the async callback only ever touches this
 * ConcurrentHashMap, and the actual PageManager call happens later, on the
 * game tick thread, from OpenShopIfRequestedAction - the same thread every
 * other Action in this plugin already runs on.
 */
public final class PendingShopOpen {

    private static final ConcurrentHashMap<UUID, Boolean> REQUESTED = new ConcurrentHashMap<>();

    private PendingShopOpen() { }

    public static void request(UUID playerUuid) {
        REQUESTED.put(playerUuid, Boolean.TRUE);
    }

    /** Get-and-clear: returns true at most once per request(). */
    public static boolean consumeIfRequested(UUID playerUuid) {
        return REQUESTED.remove(playerUuid) != null;
    }
}
