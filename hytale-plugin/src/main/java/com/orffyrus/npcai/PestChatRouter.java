package com.orffyrus.npcai;

import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Detects when a player is addressing companion {@code Pest} by name in
 * ordinary chat (e.g. "Pest, follow me", "hey Pest what's that flower?").
 *
 * Used by {@link PlayerChatToAIListener} so conversation does not require
 * a prior F-key interact when the spoken name is present. Mirrors
 * {@link MoriChatRouter} exactly, kept as a separate class (rather than
 * parameterizing one router for both names) so each companion's address
 * pattern can evolve independently without risking a regression in the
 * other - Mori is uncommitted, actively-changing WIP and Pest's brain is a
 * fundamentally different (real OpenHands) implementation, so keeping them
 * decoupled is deliberate, not duplication for its own sake.
 */
public final class PestChatRouter {

    private static final Pattern ADDRESS_PEST = Pattern.compile(
            "(?i)(?:^|\\b)pest(?:\\b|[,:!?]|$)"
    );

    private PestChatRouter() { }

    public static boolean addressesPest(String content) {
        if (content == null || content.isBlank()) {
            return false;
        }
        return ADDRESS_PEST.matcher(content.trim()).find();
    }

    /** Strip a leading "Pest," / "Pest:" so the model gets the real ask. */
    public static String stripAddress(String content) {
        if (content == null) {
            return "";
        }
        String t = content.trim();
        // remove leading Pest + optional punctuation/spaces
        String stripped = t.replaceFirst("(?i)^\\s*pest\\s*[,:!?.\\-]*\\s*", "");
        if (stripped.isBlank()) {
            return t; // bare "Pest" — keep as greet
        }
        return stripped;
    }

    public static String normalizeName() {
        return PestAdventureSpawner.PEST_DISPLAY_NAME;
    }

    public static boolean isPestRole(String roleOrName) {
        if (roleOrName == null) {
            return false;
        }
        String n = roleOrName.toLowerCase(Locale.ROOT);
        return n.equals("pest") || n.equals(PestAdventureSpawner.PEST_ROLE.toLowerCase(Locale.ROOT));
    }
}
