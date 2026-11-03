import pytest

from assistant import intent as it


def test_answer_and_command_roundtrip():
    a = it.validate({"kind": "answer", "text": " hi "})
    assert a.kind == "answer" and a.text == "hi" and not a.needs_confirm
    c = it.validate({"kind": "command", "command": "snipe", "args": ["215147", " 5000 ", "", "Vibrant Shard"]})
    assert it.build_command(c) == "!snipe 215147 5000 Vibrant Shard" and c.needs_confirm
    assert not it.validate({"kind": "command", "command": "snipes"}).needs_confirm


@pytest.mark.parametrize("obj", [
    "text", {"kind": "answer"}, {"kind": "answer", "text": "  "}, {"kind": "nope"},
    {"kind": "command", "command": "cleanup"}, {"kind": "command", "command": "snipe", "args": "x"},
    {"kind": "command", "command": "snipe", "args": [1]},
    {"kind": "command", "command": "snipe", "args": ["!cleanup"]},
    {"kind": "command", "command": "snipe", "args": ["a\nb"]},
    {"kind": "command", "command": "snipe", "args": ["@everyone"]},
    {"kind": "command", "command": "snipe", "args": ["<@123>"]},
    {"kind": "command", "command": "snipe", "args": ["`x`"]},
    {"kind": "command", "command": "snipe", "args": ["x" * 101]},
    {"kind": "command", "command": "snipe", "args": ["a"] * 21},
])
def test_rejects(obj):
    with pytest.raises(it.IntentError):
        it.validate(obj)


# Mid-arg so .strip() can't remove them: Cc (NUL, VT, FF, FS-RS, NEL, DEL, tab), Cf (zero-width, BOM, bidi), Zl, Zp.
@pytest.mark.parametrize("ch", ["\x00", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\x7f", "\t",
                                "\u200b", "\u200d", "\ufeff", "\u202e", "\u2028", "\u2029"])
def test_rejects_control_and_separator_chars(ch):
    with pytest.raises(it.IntentError):
        it.validate({"kind": "command", "command": "snipe", "args": ["215147", "5000", f"Vibrant{ch}Shard"]})


def test_accepts_non_ascii_label():
    c = it.validate({"kind": "command", "command": "snipe", "args": ["215147", "5000", "Écaille d'Ombre"]})
    assert c.args[-1] == "Écaille d'Ombre"


def test_sets_are_disjoint_and_usage_covers_all():
    assert not (it.READ_COMMANDS & it.WRITE_COMMANDS) and set(it.USAGE) == set(it.ALL_COMMANDS)
    assert set(it.SCHEMA["properties"]["command"]["enum"]) == set(it.ALL_COMMANDS)


def test_system_prompt_lists_every_command():
    text = it.render_system_prompt(it.TEMPLATE_PATH)
    assert "{{COMMANDS}}" not in text and all(f"!{name}" in text for name in it.ALL_COMMANDS)


def test_rejects_command_too_long_for_the_confirm_prompt():
    # worst case: 20 args of 100 chars would push "I'll run `...` — confirm?" past Discord's 2000-char limit
    with pytest.raises(it.IntentError):
        it.validate({"kind": "command", "command": "tokenalert", "args": ["a" * 100] * 20})
    ok = it.validate({"kind": "command", "command": "tokenalert", "args": ["a" * 100] * 18})
    assert len(it.build_command(ok)) <= it.MAX_COMMAND_LEN
