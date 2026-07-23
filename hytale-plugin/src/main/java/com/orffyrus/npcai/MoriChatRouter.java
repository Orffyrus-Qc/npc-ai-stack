package com.orffyrus.npcai;

import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Detects when a player is addressing companion {@code Mori} by name in
 * ordinary chat (e.g. "Mori, follow me", "hey Mori what's that flower?").
 *
 * Used by {@link PlayerChatToAIListener} so conversation does not require
 * a prior F-key interact when the spoken name is present.
 */
public final class MoriChatRouter {

    private static final Pattern ADDRESS_MORI = Pattern.compile(
            "(?i)(?:^|\\b)mori(?:\\b|[,:!?]|$)"
    );

    private MoriChatRouter() { }

    public static boolean addressesMori(String content) {
        if (content == null || content.isBlank()) {
            return false;
        }
        return ADDRESS_MORI.matcher(content.trim()).find();
    }

    /** Strip a leading "Mori," / "Mori:" so the model gets the real ask. */
    public static String stripAddress(String content) {
        if (content == null) {
            return "";
        }
        String t = content.trim();
        // remove leading Mori + optional punctuation/spaces
        String stripped = t.replaceFirst("(?i)^\\s*mori\\s*[,:!?.\\-]*\\s*", "");
        if (stripped.isBlank()) {
            return t; // bare "Mori" — keep as greet
        }
        return stripped;
    }

    public static String normalizeName() {
        return MoriAdventureSpawner.MORI_DISPLAY_NAME;
    }

    public static boolean isMoriRole(String roleOrName) {
        if (roleOrName == null) {
            return false;
        }
        String n = roleOrName.toLowerCase(Locale.ROOT);
        return n.equals("mori") || n.equals(MoriAdventureSpawner.MORI_ROLE.toLowerCase(Locale.ROOT));
    }
}
