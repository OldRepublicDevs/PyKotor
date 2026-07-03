"""UTC (creature) generic: GFF-based creature definitions and inventory."""

from __future__ import annotations

from typing import TYPE_CHECKING

import kaitaistruct

from bioware_kaitai_formats.gff import Gff

from loggerplus import RobustLogger
from pykotor.common.language import LocalizedString
from pykotor.common.misc import EquipmentSlot, Game, InventoryItem, ResRef
from pykotor.common.stream import BinaryReader
from pykotor.resource.formats.gff import (
    GFF,
    GFFContent,
    GFFFieldType,
    GFFList,
    bytes_gff,
    read_gff,
    write_gff,
)
from pykotor.resource.formats.gff.gff_auto import (
    _adjust_offset_after_utf8_bom,
    _classify_gff_peek,
    _locate_binary_gff_header,
    _locate_binary_gff_header_structural,
)
from pykotor.resource.type import ResourceType

if TYPE_CHECKING:
    from pykotor.resource.type import SOURCE_TYPES, TARGET_TYPES


def _order_ids_preserving_original_slots(
    current_ids: list[int],
    original_mapping: dict[int, int],
) -> list[int]:
    """Keep original list indices stable and fill removed slots with newly-added ids.

    UTC feat/power widgets represent membership, not list order. When the UI returns checked
    values in display order, a plain sort-by-original-index appends new values after all
    retained values. That shifts every original entry after a removed item and produces noisy
    TSLPatcher diffs.
    """
    if not original_mapping:
        return list(current_ids)

    current_set: set[int] = set(current_ids)
    added_ids: list[int] = [value for value in current_ids if value not in original_mapping]
    added_iter = iter(added_ids)

    ordered: list[int] = []
    for original_id, _original_index in sorted(original_mapping.items(), key=lambda item: item[1]):
        if original_id in current_set:
            ordered.append(original_id)
            continue

        replacement = next(added_iter, None)
        if replacement is not None:
            ordered.append(replacement)

    ordered.extend(added_iter)
    return ordered


def _has_original_or_nondefault_root_value(
    utc: UTC,
    label: str,
    value: object,
    default: object,
) -> bool:
    """Return whether a default-valued optional root field should be materialized."""
    original_fields = utc._original_root_field_names
    return original_fields is None or label in original_fields or value != default


class UTC:
    """Stores creature data.

    UTC files are GFF-based format files that store creature definitions including
    stats, appearance, inventory, feats, and script hooks.

    On-disk layout: root flags and script ResRefs; a stats sub-structure with localized names,
    race/gender/appearance, abilities, HP/FP, skills (eight ranks), ``ClassList`` (levels and
    known powers), ``FeatList``, equipped slots, and ``ItemList``. It has been observed that
    missing fields use the defaults applied in ``construct_utc`` (e.g. creature size 3, several
    boolean flags 1, animation id 10000, blank script ResRefs, numeric zeros). TSL adds
    extra root fields (bonus Force, puppet assignment, etc.). Loader symbols/addresses are
    migrated to ``wiki/reverse_engineering_findings.md``.

    Note: UTC uses ``GFFContent.UTC``. ``read_utc`` validates the wire shape with
    ``kaitai_generated.gff.Gff`` so the GFF header type is ``UTC `` before ``read_gff`` /
    ``construct_utc``.
    Third-party GitHub URL lines from class and attribute docstrings are archived at
    ``wiki/reverse_engineering_findings_generics_utc_github_urls_pre_scrub.md``.

    Attributes:
    ----------
        resref: "TemplateResRef" field. The resource reference for this creature template.

        tag: "Tag" field. The tag identifier for this creature.

        comment: "Comment" field. Developer comment for this creature.

        conversation: "Conversation" field. ResRef to the dialog file for this creature.

        first_name: "FirstName" field. Localized first name of the creature.

        last_name: "LastName" field. Localized last name of the creature.

        subrace_id: "SubraceIndex" field. Subrace index identifier.

        perception_id: "PerceptionRange" field. Perception range value.

        race_id: "Race" field. Race identifier.

        appearance_id: "Appearance_Type" field. Appearance type identifier.

        gender_id: "Gender" field. Gender identifier.

        faction_id: "FactionID" field. Faction identifier.

        walkrate_id: "WalkRate" field. Walk rate identifier.

        soundset_id: "SoundSetFile" field. Soundset file identifier.

        portrait_id: "PortraitId" field. Portrait identifier.

        body_variation: "BodyVariation" field. Body variation index.

        texture_variation: "TextureVar" field. Texture variation index.

        not_reorienting: "NotReorienting" field. Whether creature should not reorient.

        party_interact: "PartyInteract" field. Whether party members can interact.

        no_perm_death: "NoPermDeath" field. Whether creature cannot permanently die.

        min1_hp: "Min1HP" field. Whether creature HP cannot go below 1.

        plot: "Plot" field. Whether creature is plot-critical.

        interruptable: "Interruptable" field. Whether creature can be interrupted.

        is_pc: "IsPC" field. Whether creature is a player character.

        disarmable: "Disarmable" field. Whether creature can be disarmed.

        alignment: "GoodEvil" field. Alignment value (good/evil axis).

        challenge_rating: "ChallengeRating" field. Challenge rating value.

        blindspot: "BlindSpot" field. Blind spot value. KotOR 2 Only.

        multiplier_set: "MultiplierSet" field. Multiplier set identifier. KotOR 2 Only.

        natural_ac: "NaturalAC" field. Natural armor class value.

        reflex_bonus: "refbonus" field. Reflex save bonus.

        willpower_bonus: "willbonus" field. Will save bonus.

        fortitude_bonus: "fortbonus" field. Fortitude save bonus.

        strength: "Str" field. Strength ability score.

        dexterity: "Dex" field. Dexterity ability score.

        constitution: "Con" field. Constitution ability score.

        intelligence: "Int" field. Intelligence ability score.

        wisdom: "Wis" field. Wisdom ability score.

        charisma: "Cha" field. Charisma ability score.

        current_hp: "CurrentHitPoints" field. Current hit points.

        max_hp: "MaxHitPoints" field. Maximum hit points.

        hp: "HitPoints" field. Base hit points.

        fp: "CurrentForce" field. Current force points.

        max_fp: "ForcePoints" field. Maximum force points.

        on_end_dialog: "ScriptEndDialogu" field. Script to run when dialog ends.

        on_blocked: "ScriptOnBlocked" field. Script to run when blocked.

        on_heartbeat: "ScriptHeartbeat" field. Script to run on heartbeat.

        on_notice: "ScriptOnNotice" field. Script to run when noticing something.

        on_spell: "ScriptSpellAt" field. Script to run when spell is cast at creature.

        on_attacked: "ScriptAttacked" field. Script to run when attacked.

        on_damaged: "ScriptDamaged" field. Script to run when damaged.

        on_disturbed: "ScriptDisturbed" field. Script to run when disturbed.

        on_end_round: "ScriptEndRound" field. Script to run at end of combat round.

        on_dialog: "ScriptDialogue" field. Script to run when dialog starts.

        on_spawn: "ScriptSpawn" field. Script to run when creature spawns.

        on_rested: "ScriptRested" field. Script to run when creature rests. Not used by engine.

        on_death: "ScriptDeath" field. Script to run when creature dies.

        on_user_defined: "ScriptUserDefine" field. Script to run on user-defined event.

        ignore_cre_path: "IgnoreCrePath" field. Whether to ignore creature pathfinding. KotOR 2 Only.

        hologram: "Hologram" field. Whether creature is a hologram. KotOR 2 Only.

        palette_id: "PaletteID" field. Palette identifier. Used in toolset only.

        bodybag_id: "BodyBag" field. Body bag identifier. Not used by the game engine.

        deity: "Deity" field. Deity name. Not used by the game engine.

        description: "Description" field. Localized description. Not used by the game engine.

        lawfulness: "LawfulChaotic" field. Lawfulness value. Not used by the game engine.

        phenotype_id: "Phenotype" field. Phenotype identifier. Not used by the game engine.

        subrace_name: "Subrace" field. Subrace name. Not used by the game engine.

        classes: List of UTCClass objects representing creature classes and levels.

        feats: List of feat identifiers.

        inventory: List of InventoryItem objects in creature's inventory.

        equipment: Dictionary mapping EquipmentSlot to InventoryItem for equipped items.
    """

    BINARY_TYPE = ResourceType.UTC

    def __init__(self):
        # Internal use only, to preserve round-trip and diff compatibility.
        self._original_feat_mapping: dict[int, int] = {}
        self._original_root_field_names: set[str] | None = None
        self._extra_unimplemented_skills: list[int] = []

        self.resref: ResRef = ResRef.from_blank()
        self.conversation: ResRef = ResRef.from_blank()
        self.tag: str = ""
        self.comment: str = ""

        self.first_name: LocalizedString = LocalizedString.from_invalid()
        self.last_name: LocalizedString = LocalizedString.from_invalid()

        self.subrace_id: int = 0
        self.portrait_id: int = 0
        self.perception_id: int = 0
        self.race_id: int = 0
        self.appearance_id: int = 0
        self.gender_id: int = 0
        self.faction_id: int = 0
        self.walkrate_id: int = 0
        self.soundset_id: int = 0
        self.save_will: int = 0
        self.save_fortitude: int = 0
        self.morale: int = 0
        self.morale_recovery: int = 0
        self.morale_breakpoint: int = 0

        self.body_variation: int = 0
        self.texture_variation: int = 0

        self.portrait_resref: ResRef = ResRef.from_blank()

        self.not_reorienting: bool = False
        self.party_interact: bool = False
        self.no_perm_death: bool = False
        self.min1_hp: bool = False
        self.plot: bool = False
        self.interruptable: bool = False
        self.is_pc: bool = False  # ???
        self.disarmable: bool = False  # ???
        self.ignore_cre_path: bool = False  # KotOR 2 Only
        self.hologram: bool = False  # KotOR 2 Only
        self.will_not_render: bool = False  # Kotor 2 Only

        self.alignment: int = 0
        self.challenge_rating: float = 0.0
        self.blindspot: float = 0.0  # KotOR 2 Only
        self.multiplier_set: int = 0  # KotOR 2 Only

        self.natural_ac: int = 0
        self.reflex_bonus: int = 0
        self.willpower_bonus: int = 0
        self.fortitude_bonus: int = 0

        self.current_hp: int = 0
        self.max_hp: int = 0
        self.hp: int = 0

        self.max_fp: int = 0
        self.fp: int = 0

        self.strength: int = 0
        self.dexterity: int = 0
        self.constitution: int = 0
        self.intelligence: int = 0
        self.wisdom: int = 0
        self.charisma: int = 0

        self.computer_use: int = 0
        self.demolitions: int = 0
        self.stealth: int = 0
        self.awareness: int = 0
        self.persuade: int = 0
        self.repair: int = 0
        self.security: int = 0
        self.treat_injury: int = 0

        self.on_end_dialog: ResRef = ResRef.from_blank()
        self.on_blocked: ResRef = ResRef.from_blank()
        self.on_heartbeat: ResRef = ResRef.from_blank()
        self.on_notice: ResRef = ResRef.from_blank()
        self.on_spell: ResRef = ResRef.from_blank()
        self.on_attacked: ResRef = ResRef.from_blank()
        self.on_damaged: ResRef = ResRef.from_blank()
        self.on_disturbed: ResRef = ResRef.from_blank()
        self.on_end_round: ResRef = ResRef.from_blank()
        self.on_dialog: ResRef = ResRef.from_blank()
        self.on_spawn: ResRef = ResRef.from_blank()
        self.on_rested: ResRef = ResRef.from_blank()
        self.on_death: ResRef = ResRef.from_blank()
        self.on_user_defined: ResRef = ResRef.from_blank()

        self.classes: list[UTCClass] = []
        self.feats: list[int] = []
        self.inventory: list[InventoryItem] = []
        self.equipment: dict[EquipmentSlot, InventoryItem] = {}

        # Deprecated:
        self.palette_id: int = 0
        self.bodybag_id: int = 1
        self.deity: str = ""
        self.description: LocalizedString = LocalizedString.from_invalid()
        self.lawfulness: int = 0
        self.phenotype_id: int = 0
        # self.on_rested: ResRef = ResRef.from_blank()
        self.subrace_name: str = ""


class UTCClass:
    """Represents a creature class with its level and known powers.

    ``ClassList`` entries store class id, level, per-day spell counts, and known power lists
    as in the UTC GFF schema (same observed layout in KotOR I and TSL). Binary-level detail is
    migrated to ``wiki/reverse_engineering_findings.md``.

    Attributes:
    ----------
        class_id: The class identifier.

        class_level: The level in this class.

        powers: List of spell/power identifiers known by this class.
    """

    def __init__(
        self,
        class_id: int,
        class_level: int = 0,
    ):
        # internal use only, to preserve the original order:
        self._original_powers_mapping: dict[int, int] = {}

        self.class_id: int = class_id
        self.class_level: int = class_level
        self.powers: list[int] = []

    def __repr__(self) -> str:
        return f"UTCClass(class_id={self.class_id}, class_level={self.class_level}, powers={self.powers})"

    def __eq__(
        self,
        other: UTCClass | object,
    ):
        if self is other:
            return True
        if isinstance(other, UTCClass):
            return self.class_id == other.class_id and self.class_level == other.class_level
        return NotImplemented  # type: ignore[no-any-return]


def construct_utc(
    gff: GFF,
) -> UTC:
    """Build UTC from GFF. Missing fields use 0 / 0.0 / empty / blank ResRef / false; lists default empty (observed retail)."""
    utc = UTC()

    root = gff.root
    utc._original_root_field_names = set(root._fields)
    # Root identity: empty strings / blank ResRefs when absent.

    utc.resref = root.acquire("TemplateResRef", ResRef.from_blank())

    utc.tag = root.acquire("Tag", "", str)

    utc.comment = root.acquire("Comment", "", str)

    utc.conversation = root.acquire("Conversation", ResRef.from_blank())

    utc.first_name = root.acquire("FirstName", LocalizedString.from_invalid())

    utc.last_name = root.acquire("LastName", LocalizedString.from_invalid())

    # Stats/appearance: numeric fields default 0 when absent.
    utc.subrace_id = root.acquire("SubraceIndex", 0)

    utc.perception_id = root.acquire("PerceptionRange", 0)

    utc.race_id = root.acquire("Race", 0)

    utc.appearance_id = root.acquire("Appearance_Type", 0)

    utc.gender_id = root.acquire("Gender", 0)

    utc.faction_id = root.acquire("FactionID", 0)

    utc.walkrate_id = root.acquire("WalkRate", 0)

    utc.soundset_id = root.acquire("SoundSetFile", 0)

    utc.portrait_id = root.acquire("PortraitId", 0)

    utc.palette_id = root.acquire("PaletteID", 0)

    utc.bodybag_id = root.acquire("BodyBag", 0)

    # TODO(th3w1zard1): Add these seemingly missing fields into UTCEditor?
    utc.portrait_resref = root.acquire("Portrait", ResRef.from_blank())
    utc.save_will = root.acquire("SaveWill", 0)
    utc.save_fortitude = root.acquire("SaveFortitude", 0)
    utc.morale = root.acquire("Morale", 0)
    utc.morale_recovery = root.acquire("MoraleRecovery", 0)
    utc.morale_breakpoint = root.acquire("MoraleBreakpoint", 0)

    utc.body_variation = root.acquire("BodyVariation", 0)

    utc.texture_variation = root.acquire("TextureVar", 0)

    utc.not_reorienting = bool(root.acquire("NotReorienting", 0))

    utc.party_interact = bool(root.acquire("PartyInteract", 0))

    utc.no_perm_death = bool(root.acquire("NoPermDeath", 0))

    utc.min1_hp = bool(root.acquire("Min1HP", 0))

    utc.plot = bool(root.acquire("Plot", 0))

    utc.interruptable = bool(root.acquire("Interruptable", 0))

    utc.is_pc = bool(root.acquire("IsPC", 0))

    utc.disarmable = bool(root.acquire("Disarmable", 0))

    utc.ignore_cre_path = bool(root.acquire("IgnoreCrePath", 0))

    utc.hologram = bool(root.acquire("Hologram", 0))

    utc.will_not_render = bool(root.acquire("WillNotRender", 0))

    utc.alignment = root.acquire("GoodEvil", 0)

    utc.challenge_rating = root.acquire("ChallengeRating", 0.0)

    utc.blindspot = root.acquire("BlindSpot", 0.0)

    utc.multiplier_set = root.acquire("MultiplierSet", 0)

    utc.natural_ac = root.acquire("NaturalAC", 0)

    utc.reflex_bonus = root.acquire("refbonus", 0)

    utc.willpower_bonus = root.acquire("willbonus", 0)

    utc.fortitude_bonus = root.acquire("fortbonus", 0)

    utc.strength = root.acquire("Str", 0)

    utc.dexterity = root.acquire("Dex", 0)

    utc.constitution = root.acquire("Con", 0)

    utc.intelligence = root.acquire("Int", 0)

    utc.wisdom = root.acquire("Wis", 0)

    utc.charisma = root.acquire("Cha", 0)

    # HP/FP fields: 0 when absent.
    utc.current_hp = root.acquire("CurrentHitPoints", 0)

    utc.max_hp = root.acquire("MaxHitPoints", 0)

    utc.hp = root.acquire("HitPoints", 0)

    utc.max_fp = root.acquire("ForcePoints", 0)

    utc.fp = root.acquire("CurrentForce", 0)

    # Script hooks: blank ResRef when absent.
    utc.on_end_dialog = root.acquire("ScriptEndDialogu", ResRef.from_blank())

    utc.on_blocked = root.acquire("ScriptOnBlocked", ResRef.from_blank())

    utc.on_heartbeat = root.acquire("ScriptHeartbeat", ResRef.from_blank())

    utc.on_notice = root.acquire("ScriptOnNotice", ResRef.from_blank())

    utc.on_spell = root.acquire("ScriptSpellAt", ResRef.from_blank())

    utc.on_attacked = root.acquire("ScriptAttacked", ResRef.from_blank())

    utc.on_damaged = root.acquire("ScriptDamaged", ResRef.from_blank())

    utc.on_disturbed = root.acquire("ScriptDisturbed", ResRef.from_blank())

    utc.on_end_round = root.acquire("ScriptEndRound", ResRef.from_blank())

    utc.on_dialog = root.acquire("ScriptDialogue", ResRef.from_blank())

    utc.on_spawn = root.acquire("ScriptSpawn", ResRef.from_blank())

    utc.on_rested = root.acquire("ScriptRested", ResRef.from_blank())

    utc.on_death = root.acquire("ScriptDeath", ResRef.from_blank())

    utc.on_user_defined = root.acquire("ScriptUserDefine", ResRef.from_blank())

    # SkillList: eight entries, rank 0 when absent (retail expects eight rows).
    # Skill order: [0] Computer Use, [1] Demolitions, [2] Stealth, [3] Awareness,
    #              [4] Persuade, [5] Repair, [6] Security, [7] Treat Injury
    if not root.exists("SkillList") or root.what_type("SkillList") != GFFFieldType.List:
        if root.exists("SkillList"):
            RobustLogger().error("SkillList in UTC's must be a GFFList, recreating now...")
            del root._fields["SkillList"]
        else:
            RobustLogger().error("SkillList must exist in UTC's, creating now...")

        # Create default SkillList with 8 empty skill entries (Rank = 0)
        skill_list = root.set_list("SkillList", GFFList())
        skill_list.add(0).set_uint8("Rank", 0)  # Computer Use
        skill_list.add(1).set_uint8("Rank", 0)  # Demolitions
        skill_list.add(2).set_uint8("Rank", 0)  # Stealth
        skill_list.add(3).set_uint8("Rank", 0)  # Awareness
        skill_list.add(4).set_uint8("Rank", 0)  # Persuade
        skill_list.add(5).set_uint8("Rank", 0)  # Repair
        skill_list.add(6).set_uint8("Rank", 0)  # Security
        skill_list.add(7).set_uint8("Rank", 0)  # Treat Injury
    skill_list_acquired: GFFList = root.acquire("SkillList", GFFList())

    # Parse each skill from SkillList array by index
    if skill_list_acquired.at(0) is not None:
        skill_struct = skill_list_acquired.at(0)
        assert skill_struct is not None, "SkillList[0] struct is None"

        utc.computer_use = skill_struct.acquire("Rank", 0)  # Skill index 0: Computer Use
    if skill_list_acquired.at(1) is not None:
        skill_struct = skill_list_acquired.at(1)
        assert skill_struct is not None, "SkillList[1] struct is None"
        utc.demolitions = skill_struct.acquire("Rank", 0)  # Skill index 1: Demolitions
    if skill_list_acquired.at(2) is not None:
        skill_struct = skill_list_acquired.at(2)
        assert skill_struct is not None, "SkillList[2] struct is None"
        utc.stealth = skill_struct.acquire("Rank", 0)  # Skill index 2: Stealth
    if skill_list_acquired.at(3) is not None:
        skill_struct = skill_list_acquired.at(3)
        assert skill_struct is not None, "SkillList[3] struct is None"
        utc.awareness = skill_struct.acquire("Rank", 0)  # Skill index 3: Awareness
    if skill_list_acquired.at(4) is not None:
        skill_struct = skill_list_acquired.at(4)
        assert skill_struct is not None, "SkillList[4] struct is None"
        utc.persuade = skill_struct.acquire("Rank", 0)  # Skill index 4: Persuade
    if skill_list_acquired.at(5) is not None:
        skill_struct = skill_list_acquired.at(5)
        assert skill_struct is not None, "SkillList[5] struct is None"
        utc.repair = skill_struct.acquire("Rank", 0)  # Skill index 5: Repair
    if skill_list_acquired.at(6) is not None:
        skill_struct = skill_list_acquired.at(6)
        assert skill_struct is not None, "SkillList[6] struct is None"
        utc.security = skill_struct.acquire("Rank", 0)  # Skill index 6: Security
    if skill_list_acquired.at(7) is not None:
        skill_struct = skill_list_acquired.at(7)
        assert skill_struct is not None, "SkillList[7] struct is None"
        utc.treat_injury = skill_struct.acquire("Rank", 0)  # Skill index 7: Treat Injury

    # Some KotOR 1 UTC files contain more than eight skill rows (up to 20). PyKotor preserves
    # extras in _extra_unimplemented_skills for round-trip. Third-party templates that cap at eight
    # skills are noted in wiki *resource/generics/utc.py — SkillList*.
    if len(skill_list_acquired._structs) > 8:
        utc._extra_unimplemented_skills = [
            skill_struct.acquire("Rank", 0) for skill_struct in skill_list_acquired._structs[8:]
        ]

    # ClassList: class id/level 0; empty power lists when absent.
    class_list: GFFList = root.acquire("ClassList", GFFList())
    for class_struct in class_list:
        class_id = class_struct.acquire(
            "Class", 0
        )  # Class type identifier (e.g., 0=Soldier, 1=Scout)
        class_level = class_struct.acquire("ClassLevel", 0)  # Level in this class
        utc_class = UTCClass(class_id, class_level)

        # KnownList0 contains spells/powers known by this class level
        power_list: GFFList = class_struct.acquire("KnownList0", GFFList())
        for index, power_struct in enumerate(power_list):
            spell_thing = power_struct.acquire("Spell", 0)  # Spell/power ID
            utc_class.powers.append(spell_thing)
            # PyKotor-specific: Preserve original order for round-trip compatibility
            utc_class._original_powers_mapping[spell_thing] = index

        utc.classes.append(utc_class)

    # FeatList: feat id 0 per row when absent.
    feat_list: GFFList = root.acquire("FeatList", GFFList())
    for index, feat_struct in enumerate(feat_list):
        feat_id_thing: int = feat_struct.acquire("Feat", 0)  # Feat identifier
        utc.feats.append(feat_id_thing)
        # PyKotor-specific: Preserve original order for round-trip compatibility
        utc._original_feat_mapping[feat_id_thing] = index

    # Equip_ItemList: blank ResRef, Dropable 0 when absent.
    equipment_list: GFFList = root.acquire("Equip_ItemList", GFFList())
    for equipment_struct in equipment_list:
        # struct_id maps to EquipmentSlot enum (e.g., 0=Right Hand, 1=Left Hand, 2=Armor)
        slot = EquipmentSlot(equipment_struct.struct_id)  # Equipment slot from struct_id
        resref = equipment_struct.acquire("EquippedRes", ResRef.from_blank())  # Item ResRef
        droppable = bool(equipment_struct.acquire("Dropable", 0))  # Whether item can be dropped
        utc.equipment[slot] = InventoryItem(resref, droppable)

    # ItemList: blank ResRef, Dropable 0 when absent.
    item_list: GFFList = root.acquire("ItemList", GFFList())
    for item_struct in item_list:
        resref = item_struct.acquire("InventoryRes", ResRef.from_blank())  # Item ResRef
        droppable = bool(item_struct.acquire("Dropable", 0))  # Whether item can be dropped
        utc.inventory.append(InventoryItem(resref, droppable))

    return utc


def dismantle_utc(
    utc: UTC,
    game: Game = Game.K2,
    *,
    use_deprecated: bool = True,
) -> GFF:
    """Build UTC GFF from UTC. Written values mirror the construct path / observed retail."""
    gff = GFF(GFFContent.UTC)

    root = gff.root
    # Root fields: same defaults as on read (0, 0.0, "", blank ResRef).
    root.set_resref("TemplateResRef", utc.resref)
    root.set_string("Tag", utc.tag)
    root.set_string("Comment", utc.comment)
    root.set_resref("Conversation", utc.conversation)

    root.set_locstring("FirstName", utc.first_name)
    root.set_locstring("LastName", utc.last_name)

    root.set_uint8("SubraceIndex", utc.subrace_id)
    root.set_uint8("PerceptionRange", utc.perception_id)
    root.set_uint8("Race", utc.race_id)
    root.set_uint16("Appearance_Type", utc.appearance_id)
    root.set_uint8("Gender", utc.gender_id)
    root.set_uint16("FactionID", utc.faction_id)
    root.set_int32("WalkRate", utc.walkrate_id)
    root.set_uint16("SoundSetFile", utc.soundset_id)
    root.set_uint16("PortraitId", utc.portrait_id)

    # TODO(th3w1zard1): Add these seemingly missing fields into UTCEditor?
    if _has_original_or_nondefault_root_value(
        utc, "Portrait", utc.portrait_resref, ResRef.from_blank()
    ):
        root.set_resref("Portrait", utc.portrait_resref)
    if _has_original_or_nondefault_root_value(utc, "SaveWill", utc.save_will, 0):
        root.set_uint8("SaveWill", utc.save_will)
    if _has_original_or_nondefault_root_value(utc, "SaveFortitude", utc.save_fortitude, 0):
        root.set_uint8("SaveFortitude", utc.save_fortitude)
    if _has_original_or_nondefault_root_value(utc, "Morale", utc.morale, 0):
        root.set_uint8("Morale", utc.morale)
    if _has_original_or_nondefault_root_value(utc, "MoraleRecovery", utc.morale_recovery, 0):
        root.set_uint8("MoraleRecovery", utc.morale_recovery)
    if _has_original_or_nondefault_root_value(utc, "MoraleBreakpoint", utc.morale_breakpoint, 0):
        root.set_uint8("MoraleBreakpoint", utc.morale_breakpoint)

    root.set_uint8("BodyVariation", utc.body_variation)
    root.set_uint8("TextureVar", utc.texture_variation)

    root.set_uint8("NotReorienting", utc.not_reorienting)
    root.set_uint8("PartyInteract", utc.party_interact)
    root.set_uint8("NoPermDeath", utc.no_perm_death)
    root.set_uint8("Min1HP", utc.min1_hp)
    root.set_uint8("Plot", utc.plot)
    root.set_uint8("Interruptable", utc.interruptable)
    root.set_uint8("IsPC", utc.is_pc)
    root.set_uint8("Disarmable", utc.disarmable)

    root.set_uint8("GoodEvil", utc.alignment)
    root.set_single("ChallengeRating", utc.challenge_rating)

    root.set_uint8("NaturalAC", utc.natural_ac)
    root.set_int16("refbonus", utc.reflex_bonus)
    root.set_int16("willbonus", utc.willpower_bonus)
    root.set_int16("fortbonus", utc.fortitude_bonus)

    root.set_uint8("Str", utc.strength)
    root.set_uint8("Dex", utc.dexterity)
    root.set_uint8("Con", utc.constitution)
    root.set_uint8("Int", utc.intelligence)
    root.set_uint8("Wis", utc.wisdom)
    root.set_uint8("Cha", utc.charisma)

    root.set_int16("CurrentHitPoints", utc.current_hp)
    root.set_int16("MaxHitPoints", utc.max_hp)
    root.set_int16("HitPoints", utc.hp)
    root.set_int16("CurrentForce", utc.fp)
    root.set_int16("ForcePoints", utc.max_fp)

    root.set_resref("ScriptEndDialogu", utc.on_end_dialog)
    root.set_resref("ScriptOnBlocked", utc.on_blocked)
    root.set_resref("ScriptHeartbeat", utc.on_heartbeat)
    root.set_resref("ScriptOnNotice", utc.on_notice)
    root.set_resref("ScriptSpellAt", utc.on_spell)
    root.set_resref("ScriptAttacked", utc.on_attacked)
    root.set_resref("ScriptDamaged", utc.on_damaged)
    root.set_resref("ScriptDisturbed", utc.on_disturbed)
    root.set_resref("ScriptEndRound", utc.on_end_round)
    root.set_resref("ScriptDialogue", utc.on_dialog)
    root.set_resref("ScriptSpawn", utc.on_spawn)
    root.set_resref("ScriptRested", utc.on_rested)
    root.set_resref("ScriptDeath", utc.on_death)
    root.set_resref("ScriptUserDefine", utc.on_user_defined)

    root.set_uint8("PaletteID", utc.palette_id)

    skill_list: GFFList = root.set_list("SkillList", GFFList())
    skill_list.add(0).set_uint8("Rank", utc.computer_use)
    skill_list.add(0).set_uint8("Rank", utc.demolitions)
    skill_list.add(0).set_uint8("Rank", utc.stealth)
    skill_list.add(0).set_uint8("Rank", utc.awareness)
    skill_list.add(0).set_uint8("Rank", utc.persuade)
    skill_list.add(0).set_uint8("Rank", utc.repair)
    skill_list.add(0).set_uint8("Rank", utc.security)
    skill_list.add(0).set_uint8("Rank", utc.treat_injury)

    class_list: GFFList = root.set_list("ClassList", GFFList())
    for utc_class in utc.classes:
        class_struct = class_list.add(2)
        class_struct.set_int32("Class", utc_class.class_id)
        class_struct.set_int16("ClassLevel", utc_class.class_level)
        power_list: GFFList = class_struct.set_list("KnownList0", GFFList())
        ordered_powers = _order_ids_preserving_original_slots(
            utc_class.powers,
            utc_class._original_powers_mapping,
        )
        for power in ordered_powers:
            power_struct = power_list.add(3)
            power_struct.set_uint16("Spell", power)
            power_struct.set_uint8("SpellFlags", 1)
            power_struct.set_uint8("SpellMetaMagic", 0)

    feat_list: GFFList = root.set_list("FeatList", GFFList())
    ordered_feats = _order_ids_preserving_original_slots(utc.feats, utc._original_feat_mapping)
    for feat in ordered_feats:
        feat_list.add(1).set_uint16("Feat", feat)

    # Not sure what these are for, verified they exist in K1's 'c_drdg.utc' in data\templates.bif. Might be unused in which case this can be deleted.
    if utc._extra_unimplemented_skills:
        for val in utc._extra_unimplemented_skills:
            skill_list.add(0).set_uint8("Rank", val)

    equipment_list: GFFList = root.set_list("Equip_ItemList", GFFList())
    for slot, item in utc.equipment.items():
        equipment_struct = equipment_list.add(slot.value)
        equipment_struct.set_resref("EquippedRes", item.resref)
        if item.droppable:
            equipment_struct.set_uint8("Dropable", value=True)

    item_list: GFFList = root.set_list("ItemList", GFFList())
    for i, item in enumerate(utc.inventory):
        item_struct = item_list.add(i)
        item_struct.set_resref("InventoryRes", item.resref)
        item_struct.set_uint16("Repos_PosX", i)
        item_struct.set_uint16("Repos_Posy", 0)
        if item.droppable:
            item_struct.set_uint8("Dropable", value=True)

    if game.is_k2():
        root.set_single("BlindSpot", utc.blindspot)
        root.set_uint8("MultiplierSet", utc.multiplier_set)
        root.set_uint8("IgnoreCrePath", utc.ignore_cre_path)
        root.set_uint8("Hologram", utc.hologram)
        root.set_uint8("WillNotRender", utc.will_not_render)

    if use_deprecated:
        root.set_uint8("BodyBag", utc.bodybag_id)
        root.set_string("Deity", utc.deity)
        root.set_locstring("Description", utc.description)
        root.set_uint8("LawfulChaotic", utc.lawfulness)
        root.set_int32("Phenotype", utc.phenotype_id)
        root.set_resref("ScriptRested", utc.on_rested)
        root.set_string("Subrace", utc.subrace_name)
        root.set_list("SpecAbilityList", GFFList())
        root.set_list("TemplateList", GFFList())

    return gff


def _validate_utc_kaitai_binary(
    source: SOURCE_TYPES,
    offset: int,
    size: int | None,
) -> None:
    """If ``source`` resolves to binary GFF, parse with ``Gff`` and require header type ``UTC ``.

    XML/JSON GFF paths are skipped. On ``KaitaiStructError``, fall through so ``read_gff`` reports errors.
    Mirrors ``read_gff`` header/BOM handling so the validated slice matches ``GFFBinaryReader``.
    """
    offset_adj, size_adj = _adjust_offset_after_utf8_bom(source, offset, size)
    try:
        with BinaryReader.from_auto(source, offset_adj) as reader:
            chunk_len = min(512, max(0, reader.remaining()))
            peek = reader.read_bytes(chunk_len)
    except (FileNotFoundError, PermissionError, IsADirectoryError):
        raise
    except OSError:
        return

    header_off = _locate_binary_gff_header(peek)
    if header_off is None:
        header_off = _locate_binary_gff_header_structural(source, offset_adj, size_adj)

    promoted_binary = False
    file_format = _classify_gff_peek(peek)
    if file_format == ResourceType.INVALID and header_off is not None:
        file_format = ResourceType.GFF
        promoted_binary = True
    if (
        file_format == ResourceType.GFF
        and header_off is not None
        and (promoted_binary or header_off > 0)
    ):
        offset_adj += header_off
        if size_adj is not None:
            size_adj = max(0, size_adj - header_off)

    if file_format != ResourceType.GFF:
        return

    try:
        with BinaryReader.from_auto(source, offset_adj, size_adj) as reader:
            payload = reader.read_all()
    except OSError:
        return

    try:
        parsed = Gff.from_bytes(payload)
    except kaitaistruct.KaitaiStructError:
        return

    if parsed.header.file_type != "UTC ":
        msg = "Not a valid binary UTC file: GFF header file type is not 'UTC '."
        raise ValueError(msg)


def read_utc(
    source: SOURCE_TYPES,
    offset: int = 0,
    size: int | None = None,
) -> UTC:
    _validate_utc_kaitai_binary(source, offset, size)
    gff: GFF = read_gff(source, offset, size)
    return construct_utc(gff)


def write_utc(
    utc: UTC,
    target: TARGET_TYPES,
    game: Game = Game.K2,
    file_format: ResourceType = ResourceType.GFF,
    *,
    use_deprecated: bool = True,
):
    gff: GFF = dismantle_utc(utc, game, use_deprecated=use_deprecated)
    write_gff(gff, target, file_format)


def bytes_utc(
    utc: UTC,
    game: Game = Game.K2,
    file_format: ResourceType = ResourceType.GFF,
    *,
    use_deprecated: bool = True,
) -> bytes:
    gff: GFF = dismantle_utc(utc, game, use_deprecated=use_deprecated)
    return bytes_gff(gff, file_format)
