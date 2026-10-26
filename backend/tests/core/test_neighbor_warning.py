from app.core.neighbor_warning import compute_neighbor_warning


def test_warns_on_mayordomo_and_soy_el_nuevo_cases():
    after = compute_neighbor_warning(
        "Un mayordomo cuida este lugar.",
        "Un mayordomo cuida este lugar.\nHablaste con él por teléfono.",
        None, "-Hablaste con él por teléfono.\n-Cierto.")
    assert after and after["side"] == "after"
    soy = compute_neighbor_warning(
        "Hola, señor.", "Hola, señor.\nSoy el nuevo...", None, "-Soy el...\n-¿El nuevo?")
    assert soy and soy["side"] == "after" and soy["words"] == ["nuevo"]


def test_mascarilla_case_depends_on_long_word_share_threshold(monkeypatch):
    """36.2도 사례: 추가 단어 중 긴 단어가 ahora/quitate/mascarilla 셋이고 mascarilla만 이웃과 겹쳐 비율이 1/3이다.
    기본값(0.5)에서는 못 잡는 알려진 한계이고, 하한을 낮추면 잡힌다 — 값은 측정으로 정할 일이다."""
    args = ("36.2 °C. Todo bien.", "36.2 °C. Está bien.\nAhora quítate la mascarilla.",
            "Quédate ahí.", "-No necesitas mascarilla.\n-Está bien.")
    assert compute_neighbor_warning(*args) is None
    monkeypatch.setattr("app.core.neighbor_warning.MIN_LONG_WORD_SHARE", 0.25)
    w = compute_neighbor_warning(*args)
    assert w["side"] == "after" and "mascarilla" in w["words"]


def test_warns_when_short_addition_equals_a_whole_neighbor_sentence():
    w = compute_neighbor_warning("¿Yo?", "¿Yo?\n¡Rápido!", before_text="¡Rápido!", after_text=None)
    assert w == {"side": "before", "neighbor_text": "¡Rápido!", "words": ["rapido"]}
    sigueme = compute_neighbor_warning(
        "Solo lo explicaré una vez.", "Solo lo explicaré una vez.\nSígueme.", None, "Sígueme.")
    assert sigueme and sigueme["words"] == ["sigueme"]


def test_short_addition_matching_only_part_of_a_neighbor_sentence_is_not_flagged():
    """추가분 `no`가 이웃 문장 `No. Supongo…`의 일부 단어와 겹쳐도 문장 통째로 같지 않으면 경고하지 않는다."""
    w = compute_neighbor_warning("¿Sabes la dirección?", "¿No sabes la dirección?", None, "No sé qué hacer ahora.")
    assert w is None


def test_ordinary_correction_gets_no_warning():
    assert compute_neighbor_warning(
        "Iré más tarde si puedo.", "Si puedo, iré aunque sea tarde.",
        before_text="Vale.", after_text="¿Dónde estás?") is None


def test_common_short_words_alone_do_not_trigger_the_long_word_rule():
    assert compute_neighbor_warning(
        "Es mi día.", "Es mi primer día de la semana.", before_text="de la que", after_text="el de la") is None


def test_no_neighbors_and_html_tags_are_safe():
    assert compute_neighbor_warning("a b c", "a b c d e f", None, None) is None
    w = compute_neighbor_warning("<i>Hola.</i>", "<i>Hola.</i>\n<i>Adelante</i>", None, "Adelante.")
    assert w and w["words"] == ["adelante"]


def test_picks_neighbor_with_more_shared_words():
    w = compute_neighbor_warning(
        "Texto.", "Texto. Necesitas mascarilla quirúrgica.",
        before_text="mascarilla", after_text="Necesitas mascarilla quirúrgica ahora.")
    assert w["side"] == "after"



def test_word_already_in_original_is_not_new_content():
    """회귀: `4 segundos, 5 segundos…`를 늘린 제안은 segundos를 새로 도입한 것이 아니라 반복한 것이다."""
    w = compute_neighbor_warning(
        "4 segundos, 5 segundos,\n6 segundos, 7 segundos, 8 segundos...",
        "4 segundos, 5 segundos, 6 segundos, 7 segundos,\n8 segundos, 9 segundos, 10 segundos.",
        before_text="1 segundo, 2 segundos, 3 segundos...", after_text=None)
    assert w is None
