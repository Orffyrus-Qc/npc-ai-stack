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
 * In-game control for the Mori companion.
 *
 * <pre>
 * /mori              → help
 * /mori help         → help
 * /mori status       → session / companion / bridge flags
 * /mori spawn        → spawn if not already this session
 * /mori spawn force  → clear flag and spawn again (may leave orphan NPCs)
 * /mori creative on|off → toggle auto-spawn in Creative mode
 * </pre>
 */
public class MoriCommand extends AbstractPlayerCommand {

    private final DefaultArg<String> actionArg;
    private final DefaultArg<String> optionArg;

    public MoriCommand() {
        super("mori", "Mori adventure companion — spawn, status, help");
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
        addAliases("companion");
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
                    MoriAdventureSpawner.statusLine(playerRef.getUuid())));
            case "spawn" -> {
                boolean force = "force".equals(option);
                MoriAdventureSpawner.spawnForPlayer(
                        playerRef,
                        force,
                        force ? "command force" : "command",
                        msg -> context.sendMessage(Message.raw(msg)));
            }
            case "creative" -> {
                if ("on".equals(option) || "true".equals(option) || "1".equals(option)) {
                    MoriAdventureSpawner.AUTO_SPAWN_IN_CREATIVE = true;
                    context.sendMessage(Message.raw("Mori auto-spawn in Creative: ON"));
                } else if ("off".equals(option) || "false".equals(option) || "0".equals(option)) {
                    MoriAdventureSpawner.AUTO_SPAWN_IN_CREATIVE = false;
                    context.sendMessage(Message.raw("Mori auto-spawn in Creative: OFF"));
                } else {
                    context.sendMessage(Message.raw(
                            "Mori auto-spawn in Creative: "
                                    + (MoriAdventureSpawner.AUTO_SPAWN_IN_CREATIVE ? "ON" : "OFF")
                                    + " — use /mori creative on|off"));
                }
            }
            case "help", "" -> sendHelp(context);
            default -> {
                // Allow "/mori force" as shortcut for spawn force
                if ("force".equals(action)) {
                    MoriAdventureSpawner.spawnForPlayer(
                            playerRef, true, "command force",
                            msg -> context.sendMessage(Message.raw(msg)));
                } else {
                    context.sendMessage(Message.raw("Unknown /mori action: " + action));
                    sendHelp(context);
                }
            }
        }
    }

    private static void sendHelp(CommandContext context) {
        context.sendMessage(Message.raw(
                "Mori companion commands:\n"
                        + "  /mori status\n"
                        + "  /mori spawn\n"
                        + "  /mori spawn force\n"
                        + "  /mori creative on|off\n"
                        + "Chat: Mori, hello  |  Mori help me craft"));
    }

    private static String safe(String s) {
        return s == null ? "" : s.trim();
    }
}
