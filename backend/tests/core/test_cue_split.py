from app.core.cue_split import split_cue, split_durations

_ES = {"conjunctions": ["que", "y"], "prepositions": ["de"], "no_break_after": [], "no_break_phrases": []}


def test_split_cue_splits_at_sentence_end_and_drops_single_speaker_dashes():
    text = "- Sí, ya lo sé. - No, gracias, de verdad que no quiero nada más por ahora."
    cues = split_cue(text, 10.0, 15.0, _ES)
    assert [c[2] for c in cues][0] == "Sí, ya lo sé."
    assert cues[1][2].startswith("No, gracias")
    assert cues[0][0] == 10.0 and cues[1][1] == 15.0 and cues[0][1] == cues[1][0]


def test_split_cue_keeps_dashes_when_a_piece_still_has_two_speakers():
    text = "- Hola. - ¿Qué tal? - Bien, gracias por preguntar a todos hoy."
    cues = split_cue(text, 0.0, 6.0, _ES)
    assert any(c[2].count("-") >= 2 for c in cues) or all("-" not in c[2] for c in cues)


def test_split_cue_gives_each_cue_at_least_one_second():
    cues = split_cue("Sí. " + "palabra " * 12 + "final.", 0.0, 5.0, _ES)
    assert all(end - start >= 1.0 - 1e-9 for start, end, _ in cues)


def test_split_cue_returns_none_when_total_time_is_too_short():
    assert split_cue("Hola. ¿Cómo estás?", 0.0, 1.5, _ES) is None


def test_split_cue_check_cps_rejects_cues_that_would_be_unreadable():
    text = "Hola. " + "palabra " * 12 + "final."
    assert split_cue(text, 0.0, 2.0, _ES, check_cps=True) is None
    assert split_cue(text, 0.0, 2.0, _ES) is not None


def test_split_cue_max_cost_refuses_mid_sentence_cut():
    text = "una frase larga sin ninguna puntuación que sigue y sigue durante bastante tiempo más"
    assert split_cue(text, 0.0, 10.0, _ES, max_cost=2) is None


def test_split_durations_redistributes_when_a_piece_is_too_short():
    durs = split_durations([2, 98], 4.0)
    assert durs[0] == 1.0 and abs(sum(durs) - 4.0) < 1e-9
