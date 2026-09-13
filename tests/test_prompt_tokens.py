from app.prompt_tokens import normalize, normalize_name, tokenize


def test_basic_split_and_normalise():
    prompt = "masterpiece, (long hair:1.2), [detailed], \\(cosplay\\)"
    assert tokenize(prompt) == ["masterpiece", "long hair", "detailed", "(cosplay)"]


def test_weights_nesting_and_brace_styles():
    assert tokenize("((masterpiece)), (best quality:1.3), {blue eyes}, [smile:0.8]") == [
        "masterpiece", "best quality", "blue eyes", "smile",
    ]
    assert tokenize("(((deeply (nested:1.1) text)))") == ["deeply nested text"]


def test_escaped_parens_match_dictionary_form():
    # Dictionary side keeps its parentheses; prompt side escapes them. Both normalise the same.
    assert normalize_name("hatsune_miku_(cosplay)") == "hatsune miku (cosplay)"
    assert normalize("hatsune miku \\(cosplay\\)") == "hatsune miku (cosplay)"
    assert normalize("hatsune_miku_\\(cosplay\\)") == normalize_name("hatsune_miku_(cosplay)")
    # Unescaped parentheses in a prompt are emphasis, so they do not survive.
    assert normalize("hatsune miku (cosplay)") == "hatsune miku cosplay"


def test_case_underscore_and_whitespace():
    assert normalize("  Long_Hair  ") == "long hair"
    assert normalize("blue\n  eyes") == "blue eyes"
    assert normalize("ＴＷＩＮＴＡＩＬＳ") == "ｔｗｉｎｔａｉｌｓ"  # width is not altered, only case
    assert tokenize("a,,  , b ,") == ["a", "b"]


def test_lora_break_and_embedding_are_not_words():
    prompt = "1girl <lora:detail:0.8> BREAK long hair, embedding:badhand, <hypernet:x:1>, smile"
    assert tokenize(prompt) == ["1girl", "long hair", "smile"]


def test_stray_brackets_and_empty():
    assert normalize("(unbalanced") == "unbalanced"
    assert normalize("weird)) text") == "weird text"
    assert normalize("") == ""
    assert normalize("( )") == ""
    assert tokenize(None) == []
    assert tokenize("   ") == []


def test_dictionary_name_normalisation_is_idempotent():
    for name in ["1girl", "long_hair", "hatsune_miku_(cosplay)", "re:zero_kara_hajimeru_isekai_seikatsu", "  Odd_Case "]:
        once = normalize_name(name)
        assert normalize_name(once) == once
    assert normalize_name("re:zero_kara") == "re:zero kara"
