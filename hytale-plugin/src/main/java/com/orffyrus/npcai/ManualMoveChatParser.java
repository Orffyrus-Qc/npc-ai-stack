package com.orffyrus.npcai;

import java.util.regex.Pattern;

/**
 * Recognizes a literal player movement command in chat, e.g. "walk forward
 * and jump", "jump", "move forward" - see ManualMoveState's javadoc for why
 * this exists (a real, live-confirmed "companion pathfinding gets stuck"
 * symptom).
 *
 * Deliberately a plain Java keyword parser, NOT an LLM tag (unlike
 * GUIDE_TARGET/THREAD/etc.) - two reasons: (1) this is a literal mechanical
 * command with no real ambiguity to resolve, the same class of thing
 * EXIT_WORDS ("bye"/"goodbye"/...) already handles directly in
 * PlayerChatToAIListener without an LLM round trip; (2) it needs to keep
 * working even if the orchestrator/GPU is slow or unresponsive, which is
 * exactly the situation a player trying to unstick a companion is most
 * likely to be frustrated by if this went through the normal dialogue path.
 */
public final class ManualMoveChatParser {

    private static final Pattern FORWARD_JUMP = Pattern.compile(
            "(?i)\\b(walk|move|go)?\\s*forward\\s*(and|then)?\\s*jump\\b|\\bjump\\s*(and|then)?\\s*(walk|move|go)?\\s*forward\\b"
    );
    private static final Pattern FORWARD = Pattern.compile(
            "(?i)\\b(walk|move|go)\\s*forward\\b|^forward$"
    );
    private static final Pattern JUMP = Pattern.compile("(?i)\\bjump\\b");

    private ManualMoveChatParser() { }

    /** Returns the matching Kind, or null if this line isn't a recognized
     * manual-move command. Checked most-specific-first so "walk forward
     * and jump" doesn't get short-circuited by the plain FORWARD pattern. */
    public static ManualMoveState.Kind parse(String content) {
        if (content == null) {
            return null;
        }
        String t = content.trim();
        if (t.isEmpty()) {
            return null;
        }
        if (FORWARD_JUMP.matcher(t).find()) {
            return ManualMoveState.Kind.FORWARD_JUMP;
        }
        if (FORWARD.matcher(t).find()) {
            return ManualMoveState.Kind.FORWARD;
        }
        if (JUMP.matcher(t).find()) {
            return ManualMoveState.Kind.JUMP;
        }
        return null;
    }
}
