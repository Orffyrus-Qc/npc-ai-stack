🇬🇧 [Read this in English](README.md)

```text
╔════════════════════════════════════════════════════════════════════════╗
║                                                                        ║
║         ███████╗ █████╗ ██╗     ██╗     ██╗███╗   ██╗ ██████╗          ║
║         ██╔════╝██╔══██╗██║     ██║     ██║████╗  ██║██╔════╝          ║
║         █████╗  ███████║██║     ██║     ██║██╔██╗ ██║██║  ███╗         ║
║         ██╔══╝  ██╔══██║██║     ██║     ██║██║╚██╗██║██║   ██║         ║
║         ██║     ██║  ██║███████╗███████╗██║██║ ╚████║╚██████╔╝         ║
║         ╚═╝     ╚═╝  ╚═╝╚══════╝╚══════╝╚═╝╚═╝  ╚═══╝ ╚═════╝          ║
║                                                                        ║
║    ██████╗ ██████╗ ██╗    ██╗    ███████╗ ██████╗ ███╗   ██╗███████╗   ║
║   ██╔════╝██╔═══██╗██║    ██║    ╚══███╔╝██╔═══██╗████╗  ██║██╔════╝   ║
║   ██║     ██║   ██║██║ █╗ ██║      ███╔╝ ██║   ██║██╔██╗ ██║█████╗     ║
║   ██║     ██║   ██║██║███╗██║     ███╔╝  ██║   ██║██║╚██╗██║██╔══╝     ║
║   ╚██████╗╚██████╔╝╚███╔███╔╝    ███████╗╚██████╔╝██║ ╚████║███████╗   ║
║    ╚═════╝ ╚═════╝  ╚══╝╚══╝     ╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚══════╝   ║
║                                                                        ║
║                                                                        ║
╚════════════════════════════════════════════════════════════════════════╝
```

---

# 🐄 VOUS ÊTES ENTRÉ DANS UNE FALLING COW ZONE 🐄

```text
╔═══════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                       ║
║  ⚠⚠⚠  ARRÊTEZ. LISEZ CECI AVANT DE POINTER UN VRAI SERVEUR VERS CE PROJET.  ⚠⚠⚠       ║
║                                                                                       ║
║  CE PROJET EST EN CONSTRUCTION.                                                       ║
║  IL N'A JAMAIS TOURNÉ DE BOUT EN BOUT SUR UN VRAI SERVEUR HYTALE.                     ║
║                                                                                       ║
║  VOUS ENTREZ DANS UNE FALLING COW ZONE (ZONE DE VACHE QUI TOMBE).                     ║
║  Attendez-vous à des pièces manquantes, de mauvais noms d'API, et des                 ║
║  choix de conception qui peuvent changer sans préavis.                                ║
║                                                                                       ║
║  NE traitez PAS ce dépôt comme un cerveau de PNJ prêt pour la production.             ║
║  NE présumez PAS que NpcAiBridge.java compile avec votre HytaleServer.jar.            ║
║  NE laissez PAS la boucle d'auto-amélioration des compétences tourner sans            ║
║  surveillance sur un monde exposé à de vrais joueurs.                                 ║
║                                                                                       ║
║  HYTALE EST EN ACCÈS ANTICIPÉ -- LES API DU PLUGIN CHANGENT ENTRE LES VERSIONS.       ║
║  LES RÉGLAGES GPU/PARALLÈLES SONT DES POINTS DE DÉPART, PAS DES BENCHMARKS.           ║
║                                                                                       ║
║  Si quelque chose ici fonctionne un jour, ce sera une agréable surprise --            ║
║  pas une promesse.                                                                    ║
║                                                                                       ║
║  Statut :  🚧 EN CONSTRUCTION  ·  🧪 NON TESTÉ SUR VRAI SERVEUR  ·  🐄 FALLING COW ZONE  ║
║                                                                                       ║
╚═══════════════════════════════════════════════════════════════════════════════════════╝
```

> ### 🐄 Grand avertissement (répété, exprès)
>
> **Falling Cow Zone** signifie : stack de PNJ auto-améliorante expérimentale,
> jamais testée sur un vrai serveur Hytale, non certifiée, pas un mod fini.
> À utiliser à vos risques et périls. Si une vache tombe sur votre planning
> de déploiement, c'est une météo normale dans cette zone.

### ✅ Ce qui a réellement été vérifié (2026-07-20, RTX 3060 12 Go)

Contrairement au reste de ce README, cette partie n'est pas aspirationnelle —
toute la stack Docker a réellement été lancée sur un vrai GPU et testée de
bout en bout :

- `docker compose up -d --build` → les 4 conteneurs atteignent l'état
  healthy/running.
- Inférence GPU réelle confirmée : llama.cpp a servi des réponses en
  personnage (60+ tok/s) avec le vrai modèle Qwen2.5-7B-Instruct Q4_K_M.
- **VRAM mesurée : environ 5,9 Go, stable** (au repos comme avec 4 requêtes
  simultanées — llama.cpp pré-alloue le cache KV `--parallel` au démarrage,
  il ne grossit pas sous charge), laissant ~6 Go libres sur une carte 12 Go.
- Un faux « joueur » via un client WebSocket (respectant le protocole de
  `NpcAiBridge.java`) a tenu une vraie conversation en plusieurs tours : la
  deuxième réplique du PNJ montrait une vraie continuité avec la première
  (le rappel Qdrant fonctionne), un événement `player_gave_gift` a ajusté
  `warmth` et `trust_of_player` exactement du montant attendu dans
  Postgres, et le nombre de souvenirs épisodiques dans Qdrant correspondait
  aux tours envoyés.
- Ce test a permis de trouver et corriger **quatre vrais bugs** que la
  relecture statique n'avait pas attrapés : `download_model.sh` supposait
  un modèle en un seul fichier qui n'existe pas sur HuggingFace (Qwen
  découpe q4_k_m/q5_k_m en deux fragments) ; l'utilisateur non-root de
  l'orchestrateur cassait le cache de modèle de fastembed (une régression
  du durcissement du Dockerfile) ; la borne supérieure absente de
  `qdrant-client` a résolu vers une version qui a supprimé `.search()` ;
  et `personality.py` avait un paramètre SQL non lié qui ne se révèle
  qu'en erreur face à un vrai Postgres, pas à la simple lecture du code.

**Toujours pas testé** : un vrai serveur/client Hytale, `NpcAiBridge.java`
face aux vraies API du plugin, une charge multi-PNJ soutenue, ou
`skill_writer.py` face au vrai GPU (vérifié seulement avec un faux LLM/BDD —
voir [la section sur le méta-agent rédacteur de compétences](#le-méta-agent-rédacteur-de-compétences)).

### 🐄 Un autre genre de « vérifié » : l'ébauche du plugin Hytale

[`hytale-plugin/`](hytale-plugin/) (ajouté le 2026-07-20) est un cas différent
du reste de cette section : chaque classe/méthode utilisée a été confirmée
réelle en inspectant le bytecode d'un vrai `HytaleServer.jar` installé
(v0.5.7), pas devinée depuis la documentation. C'est plus solide qu'une
supposition, mais **ça n'a jamais été compilé** — aucun JDK 25 n'était
disponible dans l'environnement qui l'a écrit. Lisez
[`hytale-plugin/README.md`](hytale-plugin/README.md) avant de faire
confiance à quoi que ce soit ici ; il liste précisément ce qui est vérifié
par bytecode contre ce qui reste un placeholder (notamment : le changement
de thread avant de toucher l'état du monde depuis le callback du bridge, et
la conversation multi-tours basée sur le chat).

---

# Stack IA de PNJ — une seule machine, GPU 8–12 Go

Des PNJ crédibles et évolutifs pour un serveur Hytale : personnalité qui
évolue, mémoire à deux niveaux, auto-amélioration des compétences en bac à
sable, le tout sur une seule machine avec un petit GPU.

### Badge de statut (édition honnête)

```text
🚧 EN CONSTRUCTION · 🧪 NON TESTÉ SUR VRAI SERVEUR · 🐄 FALLING COW ZONE
```

## Ce que ça change dans Hytale

- **Les PNJ répondent en restant dans leur personnage.** Un joueur interagit
  avec un PNJ, le plugin envoie l'événement via WebSocket, et l'orchestrateur
  construit un prompt à partir de la personnalité et de la mémoire de ce PNJ
  pour obtenir une réponse d'un modèle Qwen2.5-7B local — aucun appel cloud,
  aucun coût par message.
- **Ils se souviennent de vous, spécifiquement.** Mémoire à deux niveaux :
  les moments récents et concrets vivent dans Qdrant (épisodique), les faits
  durables sur vous et le monde sont promus dans Postgres (sémantique) et
  compressés avec le temps pour que le contexte d'un PNJ ne gonfle pas
  indéfiniment. Demandez à un forgeron une quête mentionnée trois jours plus
  tôt, il peut encore s'en souvenir.
- **Les personnalités dérivent, volontairement, dans des limites bornées.**
  Chaque PNJ a des ajustements de traits bornés et un score de confiance par
  joueur qui évoluent selon la façon dont vous le traitez, puis reviennent
  lentement vers leur base — un PNJ grincheux peut donc se réchauffer envers
  un joueur avec le temps sans devenir un autre personnage.
- **Les PNJ inactifs marmonnent tout seuls.** Des répliques d'ambiance se
  déclenchent quand il ne se passe rien, mais elles sont plafonnées et
  perdent toujours leur créneau GPU face à un joueur qui parle réellement à
  un PNJ — le dialogue n'attend jamais derrière du texte d'ambiance, et rien
  de ce que fait le LLM ne peut bloquer la boucle de jeu (timeout strict +
  réponse de secours à chaque appel).
- **Le comportement des PNJ peut s'améliorer tout seul, prudemment.** De
  nouvelles compétences `decide(state)` sont proposées, passent une
  validation dans un conteneur verrouillé et sans réseau, et n'atteignent les
  PNJ en direct que si elles réussissent — les mauvaises candidates sont
  journalisées et rejetées plutôt que déployées. Les faits et la personnalité
  peuvent être mis à jour en direct ; le code de décision, lui, ne peut pas
  s'y glisser sans validation.

> 🐄 **Note de la vache :** tout ce qui précède décrit l'expérience de jeu
> visée une fois que `NpcAiBridge.java` sera branché sur de vrais hooks
> d'événements de PNJ Hytale — ce branchage, ainsi qu'un test en conditions
> réelles sur un vrai serveur, restent encore à faire (voir l'encadré
> d'avertissement plus haut).

### Exemple concret : un forgeron, trois visites

| Visite | Ce qui se passe | Pourquoi |
|---|---|---|
| 1re rencontre | Demander une épée → *« Aye, je peux te forger une lame, mais du bon acier, ça ne se donne pas. »* | La personnalité de base (`warmth=0.3, aggression=0.4`) se traduit par « froid et brusque » ; pas encore de souvenirs, donc le prompt indique `(première rencontre)`. |
| 3 jours plus tard | Redemander → *« De retour pour cette lame ? T'as l'argent cette fois ? »* | `recall_similar()` retrouve l'échange précédent dans Qdrant et le réinjecte dans le prompt comme souvenir pertinent. |
| Après quelques cadeaux | Le ton se réchauffe vers « poli mais réservé » — mais seulement avec vous | Chaque événement `player_gave_gift` ajuste `trust_of_player` et `warmth` d'une petite quantité bornée. La confiance est suivie par paire (PNJ, joueur), donc les autres joueurs ne voient aucun changement. |

Attaquez ce même forgeron à la place, et `player_attacked_npc` fait chuter
brutalement la confiance et augmente l'agressivité — mais comme `warmth` et
`aggression` sont stockés par PNJ (et non par joueur), le forgeron devient
plus méfiant et plus agressif *envers tout le monde*, tandis que sa confiance
spécifiquement envers vous chute le plus. Laissez-le tranquille pendant
quelques semaines de jeu et les deux effets reviennent lentement vers leur
base.

## Structure

```
docker-compose.yml          toute la stack (llama.cpp + orchestrateur + BDD)
orchestrator/
  main.py                   passerelle WebSocket + gestionnaires d'événements + démon de compression
  priority_queue.py         arbitre de créneaux GPU : le dialogue gagne toujours, l'ambiance ne bloque jamais
  llm_client.py             constructeur de prompt (personnalité+mémoire -> prompt système) + client llama.cpp
  memory.py                 épisodique Qdrant + faits sémantiques Postgres + compression
  personality.py             ajustements de traits bornés, confiance par joueur, retour à la base
  skill_writer.py           méta-agent hors-ligne : historique de résultats -> compétences candidates
sandbox/
  run_skill_validation.sh   conteneurs éphémères verrouillés pour les compétences candidates
  skill_harness.py          les tests qu'une compétence doit réussir pour être promue
scripts/download_model.sh   récupère le GGUF de Qwen2.5-7B-Instruct
hytale-plugin/               projet Gradle - voir hytale-plugin/README.md
  src/main/java/com/orffyrus/npcai/
    NpcAiBridge.java        transport côté plugin (aucun couplage à l'API Hytale)
    NpcAiPlugin.java        point d'entrée (extends JavaPlugin)
    NpcInteractListener.java  relie PlayerInteractEvent au bridge
```

## Mise en route

```bash
# 1. prérequis hôte (une seule fois)
sudo apt install nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker

# 2. modèle (Q4_K_M pour 8 Go, passez q5_k_m pour 10-12 Go)
./scripts/download_model.sh

# 3. la stack
docker compose up -d --build

# 4. test rapide du LLM directement
curl -s http://localhost:8080/v1/chat/completions -d '{
  "messages":[{"role":"user","content":"Dis bonjour comme un forgeron grincheux."}],
  "max_tokens":40}' | python3 -m json.tool
```

Le plugin se connecte à `ws://<host>:8765`. Le protocole est documenté en
haut de `orchestrator/main.py`.

> 🐄 **Note de la vache :** les étapes 1 à 4 ci-dessus ont réellement été
> exécutées sur une vraie RTX 3060 - voir
> [ce qui a été vérifié](#-ce-qui-a-réellement-été-vérifié-2026-07-20-rtx-3060-12-go).
> Toujours pas fait : mesurer `--parallel` sous charge multi-joueurs
> soutenue (testé seulement jusqu'à 4 requêtes simultanées, brièvement), et
> `NpcAiBridge.java` n'a pas encore été branché sur un vrai hook
> d'événement de PNJ Hytale.

## Réglages

| Symptôme | Réglage |
|---|---|
| Le dialogue semble lent sous charge | baissez `--parallel` à 3 (moins de créneaux, mais plus rapides) ou réduisez `max_tokens` |
| Les PNJ ont besoin d'un contexte mémoire plus long | `--parallel 2` + un contexte par créneau plus grand (compromis concurrence/contexte) |
| La simulation du monde saccade pendant le dialogue | réduisez les `cpus:` de `llm-inference` ou épinglez les cœurs du serveur Hytale |
| Le bavardage d'ambiance ne se déclenche jamais | augmentez `ambient_max_in_flight` (au prix de la marge du dialogue) |
| OOM sur une carte 8 Go | passez au modèle 3B (Llama-3.2-3B / Qwen2.5-3B Q5_K_M) |

## Flux d'auto-amélioration des compétences

1. `skill_writer.py` (ou vous, à la main) écrit des compétences candidates
   `decide(state)->action` dans `sandbox/candidates/`.
2. Un cron lance `run_skill_validation.sh` pendant les créneaux à faible affluence :
   chaque candidate s'exécute dans un conteneur jetable `--network none --read-only`
   face à `skill_harness.py`.
3. Réussite -> copie horodatée dans `approved/` (le registre chargé par l'orchestrateur).
   Échec -> `rejected/` avec les logs. Chaque rejet devient un futur cas de test.
4. Retour en arrière = supprimer la dernière version approuvée.

La boucle en direct peut toujours ajuster les *faits* et la *personnalité* ;
seul le *code de comportement* passe par le sas du bac à sable. C'est cette
séparation qui empêche un PNJ auto-améliorant de devenir un serveur
auto-saboteur.

### Le méta-agent rédacteur de compétences

`orchestrator/skill_writer.py` examine l'historique récent des résultats de
chaque PNJ (nouvelle table `npc_outcome_log`) ainsi que les dernières entrées
de `sandbox/rejected/*.log`, demande au LLM de rédiger un `decide()` adapté au
motif observé (beaucoup de `player_attacked_npc` pourrait suggérer une
réaction d'auto-défense, par exemple), effectue une vérification *statique*
de syntaxe et de forme — il n'importe ni n'exécute jamais ce qu'il génère —
et met en file dans `sandbox/candidates/` tout ce qui semble plausible. Il n'a
aucun accès à `approved/` : c'est toujours l'étape 2 ci-dessus qui décide.

Lancez-le manuellement ou depuis un cron :

```bash
docker compose run --rm skill-writer --dry-run          # aperçu, n'écrit rien
docker compose run --rm skill-writer                     # écrit des candidates
docker compose run --rm skill-writer --npc-id blacksmith_01 --since-days 7
```

> 🐄 **Note de la vache :** `skill-writer` est verrouillé par profil
> (`docker compose up` ne le démarre pas) car il appelle `llm-inference`
> directement, en contournant entièrement l'arbitre de créneaux à priorité
> dialogue de l'orchestrateur en direct. Le lancer pendant que des joueurs
> sont en ligne entrera en compétition avec le vrai dialogue pour les mêmes
> créneaux `--parallel` — ne l'exécutez que pendant une fenêtre de faible
> affluence confirmée, même règle que pour `run_skill_validation.sh`. Vérifié
> de bout en bout avec un faux LLM/BDD en local ; pas encore testé sur le
> vrai GPU/modèle.

La CI exécute une version rapide de ce sas à chaque push/PR touchant
`sandbox/**` (voir
[`.github/workflows/skill-validation.yml`](.github/workflows/skill-validation.yml)) :
elle vérifie qu'une compétence de référence correcte et une compétence de
référence défectueuse dans `sandbox/examples/` réussissent/échouent toujours
comme prévu, puis valide tout ce qui est en attente dans
`sandbox/candidates/`. Ce n'est qu'une vérification rapide de forme — le
vrai sas reste le conteneur Docker verrouillé décrit ci-dessus.
