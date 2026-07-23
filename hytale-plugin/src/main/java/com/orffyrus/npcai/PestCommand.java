package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.server.core.Message;
import com.hypixel.hytale.server.core.command.system.CommandContext;
import com.hypixel.hytale.server.core.command.system.arguments.system.DefaultArg;
import com.hypixel.hytale.server.core.command.system.arguments.types.ArgTypes;
import com.hypixel.hytale.server.core.command.system.basecommands.AbstractPlayerCommand;
import com.hypixel.hytale.server.core.universe.PlayerRef;
import com.hypixel.hytale.server.core.universe.world.World;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;

/**
 * In-game control for the Pest companion. Mirrors {@link MoriCommand}.
 *
 * <pre>
 * /pest              → help
 * /pest help         → help
 * /pest status       → session / companion / bridge flags
 * /pest spawn        → spawn if not already this session
 * /pest spawn force  → clear flag and spawn again (may leave orphan NPCs)
 * /pest creative on|off → toggle auto-spawn in Creative mode
 * </pre>
 */
public class PestCommand extends AbstractPlayerCommand {

    private final DefaultArg<String> actionArg;
    private final DefaultArg<String> optionArg;

    public PestCommand() {
        super("pest", "Pest companion (real OpenHands brain) — spawn, status, help");
        this.actionArg = withDefaultArg(
                "action",
                "help | status | spawn | creative",
                ArgTypes.STRING,
                "help",
                "subcommand");
        this.optionArg = withDefaultArg(
                "option",
                "for spawn: force; for creative: on|off",
                ArgTypes.STRING,
                "",
                "optional modifier");
    }

    @Override
    protected void execute(CommandContext context,
                           Store<EntityStore> store,
                           Ref<EntityStore> ref,
                           PlayerRef playerRef,
                           World world) {
        String action = safe(actionArg.get(context)).toLowerCase();
        String option = safe(optionArg.get(context)).toLowerCase();

        switch (action) {
            case "status" -> context.sendMessage(Message.raw(
                    PestAdventureSpawner.statusLine(playerRef.getUuid())));
            case "spawn" -> {
                boolean force = "force".equals(option);
                PestAdventureSpawner.spawnForPlayer(
                        playerRef,
                        force,
                        force ? "command force" : "command",
                        msg -> context.sendMessage(Message.raw(msg)));
            }
            case "creative" -> {
                if ("on".equals(option) || "true".equals(option) || "1".equals(option)) {
                    PestAdventureSpawner.AUTO_SPAWN_IN_CREATIVE = true;
                    context.sendMessage(Message.raw("Pest auto-spawn in Creative: ON"));
                } else if ("off".equals(option) || "false".equals(option) || "0".equals(option)) {
                    PestAdventureSpawner.AUTO_SPAWN_IN_CREATIVE = false;
                    context.sendMessage(Message.raw("Pest auto-spawn in Creative: OFF"));
                } else {
                    context.sendMessage(Message.raw(
                            "Pest auto-spawn in Creative: "
                                    + (PestAdventureSpawner.AUTO_SPAWN_IN_CREATIVE ? "ON" : "OFF")
                                    + " — use /pest creative on|off"));
                }
            }
            case "help", "" -> sendHelp(context);
            default -> {
                // Allow "/pest force" as shortcut for spawn force
                if ("force".equals(action)) {
                    PestAdventureSpawner.spawnForPlayer(
                            playerRef, true, "command force",
                            msg -> context.sendMessage(Message.raw(msg)));
                } else {
                    context.sendMessage(Message.raw("Unknown /pest action: " + action));
                    sendHelp(context);
                }
            }
        }
    }

    private static void sendHelp(CommandContext context) {
        context.sendMessage(Message.raw(
                "Pest companion commands:\n"
                        + "  /pest status\n"
                        + "  /pest spawn\n"
                        + "  /pest spawn force\n"
                        + "  /pest creative on|off\n"
                        + "Chat: Pest, hello  |  Pest help me craft\n"
                        + "Pest's brain runs on real OpenHands (openhands-sdk) - replies can take "
                        + "longer than Mori's while it researches game files/wiki. Self-evolution "
                        + "runs offline (docker compose run --rm pest-evolve), never mid-game."));
    }

    private static String safe(String s) {
        return s == null ? "" : s.trim();
    }
}
