# 작품 용어집 (Title Glossary) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 작품(title)별로 고유명사(인물/장소/상호/직함) 표기를 언어별로 등록·관리하고, 검증 프롬프트에 문맥 판단용으로 주입해 회차·언어를 넘나드는 번역 일관성을 LLM의 문맥 판단으로(기계적 문자열 치환 없이) 확보한다.

**Architecture:** `glossary_entries`(title당 한국어 용어)와 `glossary_spellings`(entry당 언어별 확정 표기) 두 테이블을 새로 만든다. 분석 시작 시 이미 확정된 표기를 `[작품 용어집]` 블록으로 만들어 Claude/GPT 검증 시스템 프롬프트에 주입하고(체크리스트에 "고유명사 표기 일관성" 항목이 생김), 같은 분석 실행 중 경량 모델 호출로 새 후보를 추출해 title 단위로 upsert한다 — 이미 확정된 표기는 자동 추출이 절대 덮어쓰지 않는다("최초 확정 우선", 사람의 수동 PATCH만 덮어쓸 수 있다). 프롬프트 빌더는 `base.py`에 모아 Claude/GPT 두 provider가 강제로 같은 문구를 쓰게 한다.

**Tech Stack:** FastAPI + SQLAlchemy(Async) + Alembic(수기 마이그레이션) + pytest-asyncio(백엔드), React(프론트).

**Spec:** `docs/superpowers/specs/2026-09-07-glossary-term-consistency-design.md` (이 계획은 그 문서의 §데이터 모델, §처리 흐름, §API, §UI, §프롬프트 변경 3·4·5만 다룬다. §프롬프트 변경 1·2·6·7과 공유 프롬프트 빌더로의 추출(`base.py`)은 이미 구현되어 있다 — 이 계획이 그 기반 위에 이어붙인다.)

## Global Constraints

- 고유명사 표기 교정은 기계적 문자열 치환이 아니라 LLM의 문맥 판단으로만 이뤄진다 — `glossary.yaml` + `_apply_glossary` 방식(2026-08-18 폐기, 동성(同姓) 다른 인물 충돌 버그)으로 되돌아가지 않는다.
- 매칭 키는 항상 한국어 원문 용어다(대상언어 표기가 아니다).
- 용어집은 title 단위로만 스코프된다 — 작품 간 공유 없음.
- 이미 확정된 언어별 표기(`GlossarySpelling`)는 자동 추출이 절대 덮어쓰지 않는다 — 오직 사람이 `PATCH /glossary/{entry_id}`로만 덮어쓸 수 있다("최초 확정 우선").
- 이미 분석 완료된 회차에 소급 적용하지 않는다 — 새로 주입된 용어집은 그 다음 분석부터만 반영된다.
- `glossary_entries`(새 파라미터)는 관련된 모든 함수 시그니처에서 항상 마지막 파라미터로, 기본값과 함께(옵셔널) 추가한다 — 기존 위치 인자 호출부(`requery.py`, `tests/core/test_pipeline.py` 등)가 깨지지 않게 하기 위함이다.
- 새 색상을 만들지 않는다 — `ReviewView.jsx`의 기존 6종 `bg-finding-*` 팔레트 중 하나를 재사용한다.

---

## File Structure

**Backend — 수정:**
- `app/providers/base.py` — `CATEGORY_ENUM`에 `"glossary"`(는 이미 `schemas.py`엔 있음, 여기 상수에만 추가), `BATCH_SCOPE_INTRO`/`REQUERY_SCOPE_INTRO`를 함수로 전환, `build_verification_checklist`에 `glossary_block` 파라미터 추가, `build_glossary_block` 신설, `build_findings_schema_instruction`에 glossary 카테고리 설명 추가, ABC `correct_primary`/`verify_and_refine` 시그니처에 `glossary_entries` 추가 + stale 독스트링 수정, `extract_glossary_terms` 추상 메서드 신설.
- `app/providers/claude_client.py` — `correct_primary`에 `glossary_entries` 스레딩, import 갱신.
- `app/providers/gpt_client.py` — `verify_and_refine`에 `glossary_entries` 스레딩(기존 `"대상언어"` 리터럴은 유지), `extract_glossary_terms` 구현 신설.
- `app/providers/live.py` — 두 메서드에 `glossary_entries` 위임 추가, `extract_glossary_terms` 위임 신설.
- `app/providers/mock.py` — 두 메서드에 `glossary_entries` 파라미터 추가, `extract_glossary_terms` 스텁(`[]`) 신설.
- `app/core/pretreatment.py` — `_apply_glossary` 삭제, `run_pretreatction`(오타 아님: `run_pretreatment`) 시그니처에서 `glossary_entries` 제거.
- `app/knowledge/loader.py` — `load_glossary` 삭제, 스킵 목록에서 `"glossary.yaml"` 제거.
- `app/knowledge/glossary.yaml` — 삭제.
- `app/models.py` — `GlossaryEntry`, `GlossarySpelling` ORM 클래스 신설.
- `app/repositories.py` — `get_glossary_prompt_entries`, `upsert_glossary_extraction`, `create_glossary_entry`, `update_glossary_entry`, `delete_glossary_entry` 신설.
- `app/core/pipeline.py` — `load_glossary` 사용 제거, `run_pretreatment` 호출부 인자 수 갱신, `_extract_glossary_terms_pass` 신설, `_run_dual_verification_pass`/`run_pipeline_phase2`에 `glossary_entries` 스레딩.
- `app/background.py` — `_run_phase2_and_save`에서 `Episode` 로드 후 `get_glossary_prompt_entries`로 주입할 용어집 조회, `run_pipeline_phase2`에 전달, 결과의 `glossary_extractions`를 `upsert_glossary_extraction`으로 저장.
- `app/routers/titles.py` — `GET /titles` 응답에 `glossary` 필드 추가(N+1 방지 배치 패턴), `POST /titles/{title_id}/glossary`, `PATCH /glossary/{entry_id}`, `DELETE /glossary/{entry_id}` 신설.

**Backend — 새 마이그레이션:**
- `alembic/versions/615c994a801e_add_glossary_tables.py` (down_revision=`e2b6a5c1f908`, 현재 head 확인됨).

**Backend — 테스트:**
- `tests/test_provider_base.py` (신설) — `build_glossary_block`/`build_verification_checklist`/`build_batch_scope_intro`/`build_findings_schema_instruction`의 순수 함수 단위 테스트.
- `tests/test_claude_client.py` — glossary_entries가 시스템 프롬프트에 실제로 반영되는지 확인하는 테스트 1개 추가.
- `tests/test_gpt_client.py` — 동일한 목적의 테스트 1개 추가, `extract_glossary_terms` 테스트 추가.
- `tests/core/test_pretreatment.py` — `test_glossary_replaces_alias_with_canonical_name` 삭제, 나머지 5개 호출부에서 glossary 인자 제거.
- `tests/test_repositories.py` — `test_save_pipeline_result_persists_final_text_and_status_for_pretreatment_findings`를 CTA 패턴 기반으로 재작성(글로서리 대신), `test_upsert_glossary_extraction_never_overwrites_existing_spelling` 신설.
- `tests/test_api_titles.py` — glossary CRUD + 목록 포함 테스트 4개 추가.

**Frontend — 수정:**
- `frontend/src/api.js` — `postGlossaryEntry`, `patchGlossaryEntry`, `deleteGlossaryEntry` 함수 추가.
- `frontend/src/views/TitleArchiveList.jsx` — 캐릭터-성별 `<details>` 옆에 항상 렌더링되는 `작품 용어집` 피벗 테이블 `<details>` 블록 추가.
- `frontend/src/views/ReviewView.jsx` — `CATEGORY_LABELS`/`CATEGORY_BADGE_CLASS`에 `glossary` 항목 추가.

---

## Task 1: base.py — 프롬프트 빌더에 용어집 주입 지원 추가

**Files:**
- Modify: `backend/app/providers/base.py`
- Test: `backend/tests/test_provider_base.py` (신설)

**Interfaces:**
- Consumes: 없음(순수 함수 계층, 최하위 레이어).
- Produces: `build_glossary_block(entries: list[dict]) -> str`, `build_batch_scope_intro(glossary_block: str = "") -> str`, `build_requery_scope_intro(glossary_block: str = "") -> str`, `build_verification_checklist(language_label: str, skip_clean_line: str, glossary_block: str = "") -> str`, `build_findings_schema_instruction(lead_in: str) -> str`(카테고리 목록에 `"glossary"` 추가). `CATEGORY_ENUM`에 `"glossary"` 추가. `ModelProvider.correct_primary`/`verify_and_refine` ABC 시그니처에 `glossary_entries: Optional[List[dict]] = None` 추가. `ModelProvider.extract_glossary_terms(self, items: List[dict], profile: dict) -> List[dict]` 신설(추상 메서드).

- [ ] **Step 1: `build_glossary_block`의 실패하는 테스트 작성**

`backend/tests/test_provider_base.py` (신규 파일):

```python
from app.providers.base import (
    build_glossary_block,
    build_batch_scope_intro,
    build_requery_scope_intro,
    build_verification_checklist,
    build_findings_schema_instruction,
    BATCH_SKIP_CLEAN_LINE,
)


def test_build_glossary_block_returns_empty_string_for_no_entries():
    assert build_glossary_block([]) == ""


def test_build_glossary_block_formats_entries_with_aliases():
    entries = [
        {"korean_term": "김현", "category": "person", "canonical": "Kim Hyun",
         "aliases": ["짱구"]},
        {"korean_term": "설악산", "category": "place", "canonical": "Mount Seorak",
         "aliases": []},
    ]
    block = build_glossary_block(entries)
    assert block.startswith("[작품 용어집]\n")
    assert "- 김현 (person): Kim Hyun (별칭: 짱구)" in block
    assert "- 설악산 (place): Mount Seorak" in block
    assert "설악산 (place): Mount Seorak (별칭:" not in block
    assert block.endswith("\n\n")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_provider_base.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_glossary_block'`

- [ ] **Step 3: `build_glossary_block` 최소 구현**

`backend/app/providers/base.py`에 추가(다른 `build_*` 함수들 근처, 예: `build_naturalness_instruction_line` 다음):

```python
def build_glossary_block(entries: List[dict]) -> str:
    """작품 용어집을 검증 프롬프트에 주입할 [작품 용어집] 블록으로 만든다.
    entries가 비어 있으면(첫 회차·첫 언어라 아직 확정된 표기가 없으면) 빈
    문자열을 반환해, build_verification_checklist의 체크리스트에서 고유명사
    표기 일관성 항목 자체가 빠지게 한다."""
    if not entries:
        return ""
    lines = ["[작품 용어집]"]
    for e in entries:
        line = f"- {e['korean_term']} ({e['category']}): {e['canonical']}"
        if e.get("aliases"):
            line += f" (별칭: {', '.join(e['aliases'])})"
        lines.append(line)
    return "\n".join(lines) + "\n\n"
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_provider_base.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/providers/base.py backend/tests/test_provider_base.py
git commit -m "feat: add build_glossary_block prompt helper"
```

- [ ] **Step 6: `build_batch_scope_intro`/`build_requery_scope_intro` 함수 전환에 대한 실패하는 테스트 작성**

`backend/tests/test_provider_base.py`에 추가:

```python
def test_build_batch_scope_intro_default_is_five_step():
    intro = build_batch_scope_intro()
    assert "[5단계 체크리스트]" in intro
    assert "5개 카테고리를 억지로" in intro


def test_build_batch_scope_intro_with_glossary_is_six_step():
    intro = build_batch_scope_intro("[작품 용어집]\n- 김현 (person): Kim Hyun\n\n")
    assert "[6단계 체크리스트]" in intro
    assert "6개 카테고리를 억지로" in intro


def test_build_requery_scope_intro_default_is_five_step():
    intro = build_requery_scope_intro()
    assert "[5단계 체크리스트]" in intro


def test_build_requery_scope_intro_with_glossary_is_six_step():
    intro = build_requery_scope_intro("[작품 용어집]\n- 김현 (person): Kim Hyun\n\n")
    assert "[6단계 체크리스트]" in intro
```

- [ ] **Step 7: 테스트 실패 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_provider_base.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_batch_scope_intro'`

- [ ] **Step 8: `BATCH_SCOPE_INTRO`/`REQUERY_SCOPE_INTRO` 상수를 함수로 전환**

`backend/app/providers/base.py`에서 기존 `BATCH_SCOPE_INTRO = (...)` 상수 정의를 찾아 다음으로 교체:

```python
def build_batch_scope_intro(glossary_block: str = "") -> str:
    step_count = 6 if glossary_block else 5
    return (
        f"각 세그먼트를 먼저 전체적으로 읽고, 명백한 문제가 있다고 확신되는 경우에만 아래 [{step_count}단계 체크리스트]에서 해당하는 카테고리를 찾아 교정 사항(findings)을 작성하라. "
        f"'혹시 여기도 어느 카테고리 하나쯤 해당되지 않을까' 하는 식으로 {step_count}개 카테고리를 억지로 하나씩 끼워 맞추려 하지 마라 — 명백한 문제가 없는 세그먼트는 그냥 건너뛰어라.\n\n"
    )
```

기존 `REQUERY_SCOPE_INTRO = (...)` 상수 정의를 찾아 다음으로 교체:

```python
def build_requery_scope_intro(glossary_block: str = "") -> str:
    step_count = 6 if glossary_block else 5
    return (
        "이 세그먼트는 검수자가 이미 문제가 있다고 판단해 재검토를 요청한 것이다 — "
        "너 스스로 '문제가 명백한지' 다시 판단해 건너뛰지 말고, "
        f"아래 [{step_count}단계 체크리스트]에서 "
        "가장 가까운 카테고리를 찾아 검수자 지시사항을 반영한 교정 사항(findings)을 반드시 작성하라. "
        "이 세그먼트를 배열에서 빼는 것은 금지된다.\n"
        "⚠️ 아래 target_text는 이전 검토에서 이미 한 번 고친 결과물이다 — 네가(또는 다른 "
        "모델이) 만들었다는 이유로 이미 맞다고 안일하게 판단하지 말고, korean_text와 처음부터 "
        "다시 대조해 검수자 지시사항 관점에서 재검토하라.\n\n"
    )
```

`base.py` 안에서 이 두 상수를 참조하던 곳(`build_verification_checklist` 밖에서는 참조 없음, 두 상수는 클라이언트 파일에서만 쓰임)은 Task 2에서 갱신한다.

- [ ] **Step 9: 테스트 통과 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_provider_base.py -v`
Expected: PASS (6 tests)

- [ ] **Step 10: Commit**

```bash
git add backend/app/providers/base.py backend/tests/test_provider_base.py
git commit -m "refactor: convert scope-intro constants to glossary-aware functions"
```

- [ ] **Step 11: `build_verification_checklist`에 `glossary_block` 파라미터 추가에 대한 실패하는 테스트 작성**

`backend/tests/test_provider_base.py`에 추가:

```python
def test_build_verification_checklist_default_has_five_steps_no_glossary_item():
    checklist = build_verification_checklist("스페인어", BATCH_SKIP_CLEAN_LINE)
    assert "[5단계 순차 검증 체크리스트]" in checklist
    assert 'category: "glossary"' not in checklist
    assert "위 1~4번 문제를 고치기 위해" in checklist


def test_build_verification_checklist_with_glossary_block_has_six_steps():
    glossary_block = "[작품 용어집]\n- 김현 (person): Kim Hyun\n\n"
    checklist = build_verification_checklist("스페인어", BATCH_SKIP_CLEAN_LINE, glossary_block)
    assert "[6단계 순차 검증 체크리스트]" in checklist
    assert '5. 고유명사 표기 일관성 (category: "glossary"):' in checklist
    assert "아래 [작품 용어집]에 등록된 한국어 용어" in checklist
    assert "6. 이미 반영된 성별/격식 형태 보존:" in checklist
    assert "위 1~5번 문제를 고치기 위해" in checklist
```

- [ ] **Step 12: 테스트 실패 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_provider_base.py -v`
Expected: FAIL with `TypeError: build_verification_checklist() takes 2 positional arguments but 3 were given`

- [ ] **Step 13: `build_verification_checklist` 구현**

`backend/app/providers/base.py`의 기존 `build_verification_checklist(language_label: str, skip_clean_line: str) -> str:` 함수 전체를 다음으로 교체:

```python
def build_verification_checklist(language_label: str, skip_clean_line: str,
                                  glossary_block: str = "") -> str:
    """[검수 범위 및 교정 원칙] + [N단계 순차 검증 체크리스트]. claude/gpt가
    재질문 여부에 따라 다른 skip_clean_line만 끼워 넣고 나머지는 동일하게
    쓴다. glossary_block이 있으면(작품 용어집에 이미 확정된 표기가 하나라도
    있으면) 고유명사 표기 일관성 항목이 체크리스트에 추가되고 단계 수가
    6단계로 늘어난다 — glossary_block은 이 함수 밖(호출부)에서 실제
    [작품 용어집] 내용으로 시스템 프롬프트에 삽입된다."""
    step_count = 6 if glossary_block else 5
    glossary_item = (
        '5. 고유명사 표기 일관성 (category: "glossary"):\n'
        '   - 기준: 아래 [작품 용어집]에 등록된 한국어 용어가 korean_text에 있는데, target_text의 표기가 용어집의 표기와 다른가?\n'
        '   - 교정 지침: 용어집 표기로 통일하되, 문장 문법(관사·전치사·성수 일치)에 맞게 자연스럽게 넣어라. 용어집에 없는 고유명사는 건드리지 마라.\n'
        '   - 주의: 같은 성씨를 쓰는 다른 인물 등 문맥상 다른 대상을 가리키는 게 분명하면 교정하지 마라.\n'
        '   - [작품 용어집]의 한국어 용어는 대표형이다. 축약형·호격형(예: 김현 → 현, 현아)도 같은 대상으로 보고 같은 스펠링을 쓰되, 문장에서 실제로 부르는 형태(성+이름 전체 / 이름만)는 원문을 따라라.\n'
    ) if glossary_block else ""
    return (
        "⚠️ [검수 범위 및 교정 원칙]\n"
        "1. 반드시 교정해야 하는 대상:\n"
        "   - 오역 및 핵심 의미 누락/와전 (category: \"mistranslation\")\n"
        "   - 방송/미디어 심의 위반 비속어 (category: \"sensitivity\")\n"
        "   - 한국어 구조를 그대로 따라가 현지인이 읽기에 어색한 직역투 (category: \"unnatural_style\")\n"
        "   - 현지 문화권 관습, 관용구, 단위 표기 오류 (category: \"locale_convention\")\n"
        "   - 지정된 성별(대상언어 문법상 성별 어미) 및 격식(존댓말/반말) 파라미터 위반\n"
        "2. 교정 금지 대상 (취향 차이의 다듬기):\n"
        "   - 의미 왜곡이 없고 현지 구어체로 이미 타당한 번역인데, 단순히 AI 개인 선호 어휘나 동의어로 다듬는 수정은 제안하지 마라.\n"
        + skip_clean_line +
        "   - nuance_tone(뉘앙스·어조)은 다음 경우에만 제안하라:\n"
        f"     * 직역투로 인해 명백히 어색한 경우 (한국어 구조를 그대로 따라가 {language_label}로서 부자연스러운 경우)\n"
        "     * 한국어 원문의 감정·톤(급함, 거침, 간결함, 여유로움 등)이 명확히 다르게 전달된 경우\n"
        "   - 이미 자연스러운 구어체 표현이면 건드리지 마라. 원문의 감정·톤을 정확히 전달하고 있으면 제안하지 마라.\n\n"
        f"[{step_count}단계 순차 검증 체크리스트]\n"
        "1. 방송/미디어 심의 비속어 검수 (category: \"sensitivity\"):\n"
        "   - 기준: 영상 방영 및 미디어 심의(Broadcasting Rating)상 제재나 경고 대상이 될 수 있는 심한 비속어, 성적·인격모독적 표현이 포함되어 있는가?\n"
        "   - 교정 지침: 대사의 거친 뉘앙스는 유지하되, 방송 심의 기준에 적합한 수위가 약한 비속어나 자연스러운 순화 표현으로 교정(`corrected_text`)하라.\n"
        "2. 오역 및 핵심 의미 누락 (category: \"mistranslation\"):\n"
        "   - 기준: korean_text의 실제 의미와 target_text의 번역 의미가 다르게 와전되었거나, 문장의 핵심 의미가 생략되었는가? 또는 원문의 구체적 사실(인물·장소·숫자·행동)이 생략·변경·추가되었는가? 단, 사실이 아닌 부연 설명·수식어를 줄인 것은 여기 해당하지 않는다.\n"
        "   - 교정 지침: 원문의 뜻을 왜곡 없이 정확하게 전달하도록 교정하라.\n"
        "3. 어색한 어조 및 직역투 (category: \"unnatural_style\" 또는 \"nuance_tone\"):\n"
        f"   - 기준: 문법은 맞지만 한국어 어순/표현을 그대로 따라간 직역투라 {language_label}로서 어색한가? 또는 한국어 원문의 감정·톤이 명확히 다르게 전달되었는가?\n"
        f"   - 교정 지침: 원문의 감정·톤을 정확히 살리면서 {language_label}권 현지인이 실제로 사용하는 자연스러운 구어체로 교정하라. 자막은 화면과 함께 순간적으로 읽는 매체이니 뜻이 통하는 선에서 최대한 간결하게 써라 — 화면으로 이미 전달되는 정보나 불필요한 부연 설명은 생략하라. 같은 씬 안에서 한국어 원문의 단어/표현이 반복되면, 문법적으로 다르게 써야 할 이유가 없는 한 같은 번역으로 통일하라.\n"
        "   - 주의: 원문이 이미 자연스러운 구어체로 한국어의 감정·톤을 잘 전달하고 있으면 nuance_tone 제안을 하지 마라.\n"
        "4. 문화 맥락 및 로컬라이제이션 (category: \"locale_convention\"):\n"
        f"   - 기준: {language_label}권 문화 관습, 관용 표현, 단위 표기(미터법/화폐 등)에 안 맞는 번역이 있는가?\n"
        "   - 교정 지침: 해당 언어권의 문화적 관습과 로컬라이제이션 관례에 맞게 교정하라.\n"
        + glossary_item +
        f"{step_count}. 이미 반영된 성별/격식 형태 보존:\n"
        "   - 기준: target_text에 이미 특정 성별 어미(대상언어 문법상 형용사·분사·명사 어미)나 격식(존댓말/반말) 형태가 반영되어 있을 수 있다 — 그 형태가 사전상 어색하거나 비표준으로 보여도, 검수 과정에서 의도적으로 맞춘 것이니 임의로 '자연스럽게' 되돌리지 마라.\n"
        f"   - 교정 지침: 위 1~{step_count - 1}번 문제를 고치기 위해 교정문(`corrected_text`)을 작성할 때도, target_text에 이미 있는 성별 어미·격식 형태는 그대로 유지하라 — 오직 그 카테고리의 문제만 고쳐라.\n\n"
    )
```

- [ ] **Step 14: 테스트 통과 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_provider_base.py -v`
Expected: PASS (10 tests)

- [ ] **Step 15: Commit**

```bash
git add backend/app/providers/base.py backend/tests/test_provider_base.py
git commit -m "feat: add glossary checklist item to verification checklist builder"
```

- [ ] **Step 16: `CATEGORY_ENUM`과 `build_findings_schema_instruction`에 대한 실패하는 테스트 작성**

`backend/tests/test_provider_base.py`에 추가:

```python
from app.providers.base import CATEGORY_ENUM


def test_category_enum_includes_glossary():
    assert "glossary" in CATEGORY_ENUM


def test_build_findings_schema_instruction_includes_glossary_category():
    instruction = build_findings_schema_instruction("lead in text")
    assert '"glossary"(작품 용어집에 등록된 고유명사 표기와 다르게 번역된 경우)' in instruction
```

- [ ] **Step 17: 테스트 실패 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_provider_base.py -v`
Expected: FAIL (`"glossary" in CATEGORY_ENUM` is False, and the schema instruction lacks the new clause)

- [ ] **Step 18: `CATEGORY_ENUM`과 `build_findings_schema_instruction` 구현**

`backend/app/providers/base.py`에서 `CATEGORY_ENUM = [...]` 정의를 찾아 `"glossary"`를 추가:

```python
CATEGORY_ENUM = ["sensitivity", "mistranslation", "nuance_tone", "unnatural_style",
                  "locale_convention", "glossary"]
```

`build_findings_schema_instruction` 안에서 카테고리 열거 부분의 마지막 줄
`'"locale_convention"(그 문화권 관습·로컬라이제이션에 안 맞는 표현)), '`을 찾아
다음 두 줄로 교체(닫는 괄호 하나가 새 마지막 줄로 옮겨간다):

```python
'"locale_convention"(그 문화권 관습·로컬라이제이션에 안 맞는 표현), '
'"glossary"(작품 용어집에 등록된 고유명사 표기와 다르게 번역된 경우)), '
```

- [ ] **Step 19: 테스트 통과 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_provider_base.py -v`
Expected: PASS (12 tests)

- [ ] **Step 20: Commit**

```bash
git add backend/app/providers/base.py backend/tests/test_provider_base.py
git commit -m "feat: add glossary to CATEGORY_ENUM and findings schema instruction"
```

- [ ] **Step 21: ABC 시그니처 변경 — `correct_primary`/`verify_and_refine`에 `glossary_entries` 추가, `extract_glossary_terms` 신설**

`backend/app/providers/base.py` 상단 import에 `Optional` 추가(이미 `List`는 있다):

```python
from typing import List, Optional
```

`ModelProvider` ABC의 `correct_primary` 추상 메서드를 찾아 시그니처와 독스트링을 교체:

```python
    @abstractmethod
    async def correct_primary(self, pairs: List[dict], profile: dict,
                               pending_sensitive_hits: List[dict],
                               knowledge: str, format_constraint: str,
                               extra_instruction: str = "",
                               glossary_entries: Optional[List[dict]] = None) -> List[dict]:
        """Claude 검증 패스: 원본(korean_text/target_text)을 처음부터 독립적으로
        검토해 사전에 없는 애매한 비속어, 번역정확성·문화맥락·뉘앙스어조·
        자연스러운흐름(직역투)·함축의미·로컬라이제이션·작품 용어집 고유명사 표기
        (glossary_entries가 있을 때만) 문제를 찾아 고친다. GPT 검증 패스
        (verify_and_refine)와 동시에 같은 원본을 받아 서로 독립적으로 판단한다
        — 어느 쪽도 상대가 뭘 했는지 모른다(파이프라인이 둘의 일치/불일치를
        나중에 병합해 신뢰도 신호로 쓴다). glossary_entries는 title+language
        단위로 이미 확정된 [작품 용어집] 항목(get_glossary_prompt_entries)이다
        — 비어 있으면(첫 회차·첫 언어) 체크리스트에서 고유명사 항목 자체가
        빠진다. 성별/격식은 화자를 특정할 근거가 없어 여기서 다루지 않는다
        — check_grammar_necessity로 걸러 사람이 직접 확인한다. 변경이 필요한
        세그먼트만 반환한다. 반환값은
        [{"segment_id": str,
        "category": "sensitivity"|"mistranslation"|"nuance_tone"|"unnatural_style"|"locale_convention"|"glossary",
        "corrected_text": str, "description": str(한국어)}, ...]"""
        ...
```

바로 다음 `verify_and_refine` 추상 메서드를 찾아 시그니처와 독스트링을 교체:

```python
    @abstractmethod
    async def verify_and_refine(self, pairs: List[dict], profile: dict,
                                 pending_sensitive_hits: List[dict],
                                 knowledge: str, format_constraint: str,
                                 extra_instruction: str = "",
                                 glossary_entries: Optional[List[dict]] = None) -> List[dict]:
        """GPT 검증 패스: correct_primary와 대칭적으로, 같은 원본을 처음부터
        독립적으로 검토한다. Claude가 뭘 고쳤는지/안 고쳤는지 알려주지 않는다
        — "이전 교정을 검토"하는 프레이밍은 앵커링 편향(모델이 제시된 답을
        독립적으로 재도출하기보다 그냥 승인하는 쪽으로 기우는 현상)을 유발해
        정확도를 낮춘다. glossary_entries도 correct_primary와 동일하게 받는다.
        변경이 필요한 세그먼트만 반환한다. 반환값은 correct_primary와 동일한
        형태."""
        ...
```

`gloss_words` 추상 메서드 바로 다음, `apply_formality` 앞에 새 추상 메서드 추가:

```python
    @abstractmethod
    async def extract_glossary_terms(self, items: List[dict], profile: dict) -> List[dict]:
        """정렬된 (korean_text, target_text) 쌍에서 다른 회차·다른 언어판에서도
        표기가 일관되어야 하는 고유명사(인물/장소/상호/직함)를 찾아 이번 회차
        번역문의 표기를 뽑는다. 저장 단계(repositories.upsert_glossary_extraction)
        가 title 단위로 upsert하며, 이미 확정된 표기는 절대 덮어쓰지 않는다 —
        이 호출은 후보만 뽑고 최종 결정은 하지 않는다. 입력은
        [{"id": str, "korean_text": str, "target_text": str}, ...], 반환값은
        [{"korean_term": str, "category": "person"|"place"|"business"|"title",
        "canonical": str}, ...] — 고유명사가 없는 항목은 응답에서 빠진다."""
        ...
```

이 단계는 순수 시그니처/독스트링 변경이라 새 테스트가 필요 없다 — 대신 다음 단계에서 `mock.py`(Task 3)가 함께 갱신되기 전까지는 `MockProvider`가 ABC를 만족하지 못해 이를 인스턴스화하는 기존 테스트들이 전부 깨진다는 걸 기록해둔다(그래서 Step 22에서 즉시 확인한다).

- [ ] **Step 22: 기존 테스트 스위트가 깨지는지 확인(예상된 실패)**

Run: `cd backend && ./venv/bin/pytest tests/ -k "mock or Mock" -v`
Expected: FAIL with `TypeError: Can't instantiate abstract class MockProvider with abstract method extract_glossary_terms` — 이건 Task 3에서 고친다. 지금은 base.py 자체 테스트만 통과하면 된다:

Run: `cd backend && ./venv/bin/pytest tests/test_provider_base.py -v`
Expected: PASS (12 tests, 변경 없음)

- [ ] **Step 23: Commit**

```bash
git add backend/app/providers/base.py
git commit -m "feat: thread glossary_entries through ModelProvider ABC and add extract_glossary_terms"
```

---

## Task 2: claude_client.py / gpt_client.py — provider 구현에 용어집 스레딩

**Files:**
- Modify: `backend/app/providers/claude_client.py`
- Modify: `backend/app/providers/gpt_client.py`
- Test: `backend/tests/test_claude_client.py`
- Test: `backend/tests/test_gpt_client.py`

**Interfaces:**
- Consumes: `build_glossary_block`, `build_batch_scope_intro`, `build_requery_scope_intro`, `build_verification_checklist(language_label, skip_clean_line, glossary_block="")` (모두 Task 1의 `base.py`).
- Produces: `ClaudeClient.correct_primary(..., glossary_entries=None)`, `GptClient.verify_and_refine(..., glossary_entries=None)`, `GptClient.extract_glossary_terms(items, profile) -> list[dict]`.

- [ ] **Step 1: `correct_primary`가 glossary_entries를 프롬프트에 반영하는지 실패하는 테스트 작성**

`backend/tests/test_claude_client.py`에 추가(파일 상단에 이미 있는 `ClaudeClient`/httpx 목업 패턴을 그대로 따른다 — 기존 `test_correct_primary_system_prompt_contains_shared_verification_block` 테스트 바로 아래에 추가):

```python
@pytest.mark.asyncio
async def test_correct_primary_includes_glossary_block_when_entries_given(monkeypatch):
    captured = {}

    async def fake_call_array(self, system, user, **kwargs):
        captured["system"] = system
        return []

    monkeypatch.setattr(ClaudeClient, "_call_array", fake_call_array)
    monkeypatch.setattr(ClaudeClient, "_retry_hangul_leaks",
                         lambda self, results, *a, **kw: results)

    client = ClaudeClient(api_key="x")
    profile = {"target_language": "es", "variant": "LATAM"}
    glossary_entries = [{"korean_term": "김현", "category": "person",
                          "canonical": "Kim Hyun", "aliases": []}]
    await client.correct_primary([], profile, [], "", "", "", glossary_entries)

    assert "[작품 용어집]" in captured["system"]
    assert "김현 (person): Kim Hyun" in captured["system"]
    assert '5. 고유명사 표기 일관성 (category: "glossary"):' in captured["system"]
```

(`test_gpt_client.py`에도 대칭적으로 추가 — 기존 `test_verify_and_refine_system_prompt_contains_shared_verification_block` 바로 아래):

```python
@pytest.mark.asyncio
async def test_verify_and_refine_includes_glossary_block_when_entries_given(monkeypatch):
    captured = {}

    async def fake_call(self, system, user, **kwargs):
        captured["system"] = system
        return []

    monkeypatch.setattr(GptClient, "_call", fake_call)
    monkeypatch.setattr(GptClient, "_retry_hangul_leaks",
                         lambda self, results, *a, **kw: results)

    client = GptClient(api_key="x")
    profile = {"target_language": "es", "variant": "LATAM"}
    glossary_entries = [{"korean_term": "김현", "category": "person",
                          "canonical": "Kim Hyun", "aliases": []}]
    await client.verify_and_refine([], profile, [], "", "", "", glossary_entries)

    assert "[작품 용어집]" in captured["system"]
    assert "김현 (person): Kim Hyun" in captured["system"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_claude_client.py::test_correct_primary_includes_glossary_block_when_entries_given tests/test_gpt_client.py::test_verify_and_refine_includes_glossary_block_when_entries_given -v`
Expected: FAIL — `TypeError: correct_primary() takes from 6 to 7 positional arguments but 8 were given` (glossary_entries 파라미터가 아직 없음)

- [ ] **Step 3: `claude_client.py` import 및 `correct_primary` 구현**

`backend/app/providers/claude_client.py` 상단 import를 찾아 교체:

```python
from typing import List, Optional
from app.providers.base import (
    contains_hangul, CATEGORY_ENUM, VERIFICATION_PRIORITY_PARAGRAPH,
    build_batch_scope_intro, BATCH_SKIP_CLEAN_LINE,
    build_requery_scope_intro, REQUERY_SKIP_CLEAN_LINE,
    build_verification_checklist, build_json_instruction, build_json_instruction_requery,
    build_findings_schema_instruction, build_naturalness_instruction_line,
    build_improvement_judgment_criteria, build_glossary_block,
)
```

`correct_primary` 메서드 전체를 다음으로 교체:

```python
    async def correct_primary(self, pairs: List[dict], profile: dict,
                               pending_sensitive_hits: List[dict],
                               knowledge: str, format_constraint: str,
                               extra_instruction: str = "",
                               glossary_entries: Optional[List[dict]] = None) -> List[dict]:
        language_label = _language_label(profile)
        naturalness_instruction = (profile.get("naturalness_check") or {}).get("llm_instruction", "")
        glossary_block = build_glossary_block(glossary_entries or [])

        if extra_instruction:
            scope_intro = build_requery_scope_intro(glossary_block)
            skip_clean_line = REQUERY_SKIP_CLEAN_LINE
        else:
            scope_intro = build_batch_scope_intro(glossary_block)
            skip_clean_line = BATCH_SKIP_CLEAN_LINE

        system = (
            f"너는 한국어-{language_label} 자막의 전문 번역 검수자다. "
            f"korean_text(한국어 원문)를 절대 기준(Source of Truth)으로 삼아 target_text({language_label} 번역문)를 검증하라. "
            + scope_intro +
            VERIFICATION_PRIORITY_PARAGRAPH +
            build_verification_checklist(language_label, skip_clean_line, glossary_block) +
            f"⚠️ [자막 형태 및 글자수 절대 제약 - HARD CONSTRAINT]\n"
            f"- 모든 교정문(corrected_text)은 반드시 다음 제약을 엄격히 지켜서 작성하라: {format_constraint}\n"
            "- 각 줄의 글자수를 실제로 세어보고 제약 글자수를 초과하면 절/쉼표 경계에서 자연스럽게 줄바꿈(\\n)을 넣거나 표현을 다듬어 글자수 한도 내로 들어오게 작성하라.\n\n"
            f"참고 지식베이스: {knowledge}\n"
        )
        system += glossary_block
        json_instruction = _JSON_INSTRUCTION_REQUERY if extra_instruction else _JSON_INSTRUCTION
        output_schema = _PRIMARY_OUTPUT_SCHEMA_REQUERY if extra_instruction else _PRIMARY_OUTPUT_SCHEMA
        schema_instruction = _PRIMARY_SCHEMA_INSTRUCTION
        if extra_instruction:
            schema_instruction += "\n" + _BACK_TRANSLATION_FIELD_INSTRUCTION
        system += (
            f"사전에 없어 애매한 비속어 후보(참고용): "
            f"{json.dumps(pending_sensitive_hits, ensure_ascii=False)}\n"
        )
        system += build_naturalness_instruction_line(naturalness_instruction)
        system += json_instruction + "\n" + schema_instruction

        if extra_instruction:
            system += f"\n검수자의 추가 지시사항(반드시 반영): {extra_instruction}"
        user = json.dumps(pairs, ensure_ascii=False)
        results = await self._call_array(system, user, model=self._model, temperature=0,
                                          output_schema=output_schema)
        return await self._retry_hangul_leaks(
            results, pairs, system, language_label, model=self._model, output_schema=output_schema)
```

(이 교체에서 실제로 달라지는 줄은: 시그니처에 `glossary_entries` 추가, `glossary_block` 계산 줄 추가, `scope_intro`/`build_verification_checklist` 호출에 `glossary_block` 전달, `참고 지식베이스` 줄 바로 다음에 `system += glossary_block` 추가. 나머지는 기존 코드 그대로다.)

- [ ] **Step 4: `gpt_client.py` import 및 `verify_and_refine` 구현**

`backend/app/providers/gpt_client.py` 상단 import를 찾아 교체:

```python
from typing import List, Optional
from app.providers.base import (
    contains_hangul, CATEGORY_ENUM, VERIFICATION_PRIORITY_PARAGRAPH,
    build_batch_scope_intro, BATCH_SKIP_CLEAN_LINE,
    build_requery_scope_intro, REQUERY_SKIP_CLEAN_LINE,
    build_verification_checklist, build_json_instruction, build_json_instruction_requery,
    build_findings_schema_instruction, build_naturalness_instruction_line,
    build_improvement_judgment_criteria, build_glossary_block,
)
```

`verify_and_refine` 메서드 전체를 다음으로 교체:

```python
    async def verify_and_refine(self, pairs: List[dict], profile: dict,
                                 pending_sensitive_hits: List[dict],
                                 knowledge: str, format_constraint: str,
                                 extra_instruction: str = "",
                                 glossary_entries: Optional[List[dict]] = None) -> List[dict]:
        language_label = _language_label(profile)
        naturalness_instruction = (profile.get("naturalness_check") or {}).get("llm_instruction", "")
        glossary_block = build_glossary_block(glossary_entries or [])

        if extra_instruction:
            scope_intro = build_requery_scope_intro(glossary_block)
            skip_clean_line = REQUERY_SKIP_CLEAN_LINE
        else:
            scope_intro = build_batch_scope_intro(glossary_block)
            skip_clean_line = BATCH_SKIP_CLEAN_LINE

        system = (
            f"너는 한국어-{language_label} 자막의 전문 번역 검수자다. "
            "korean_text(한국어 원문)를 절대 기준(Source of Truth)으로 삼아 target_text(대상언어 번역문)를 검증하라. "
            + scope_intro +
            VERIFICATION_PRIORITY_PARAGRAPH +
            build_verification_checklist("대상언어", skip_clean_line, glossary_block) +
            f"⚠️ [자막 형태 및 글자수 절대 제약 - HARD CONSTRAINT]\n"
            f"- 모든 교정문(corrected_text)은 반드시 다음 제약을 엄격히 지켜서 작성하라: {format_constraint}\n"
            "- 각 줄의 글자수를 실제로 세어보고 제약 글자수를 초과하면 절/쉼표 경계에서 자연스럽게 줄바꿈(\\n)을 넣거나 표현을 다듬어 글자수 한도 내로 들어오게 작성하라.\n\n"
            f"참고 지식베이스: {knowledge}\n"
        )
        system += glossary_block

        system += (
            f"사전에 없어 애매한 비속어 후보(참고용): "
            f"{json.dumps(pending_sensitive_hits, ensure_ascii=False)}\n"
        )
        system += build_naturalness_instruction_line(naturalness_instruction)
        json_instruction = _JSON_INSTRUCTION_REQUERY if extra_instruction else _JSON_INSTRUCTION
        schema_instruction = _VERIFY_SCHEMA_INSTRUCTION
        if extra_instruction:
            schema_instruction += "\n" + _BACK_TRANSLATION_FIELD_INSTRUCTION
        system += json_instruction + "\n" + schema_instruction
        if extra_instruction:
            system += f"\n검수자의 추가 지시사항(반드시 반영): {extra_instruction}"
        user = json.dumps(pairs, ensure_ascii=False)
        response_format = _FINDINGS_SCHEMA_REQUERY if extra_instruction else _FINDINGS_SCHEMA
        results = await self._call(system, user, seed=_SEED, response_format=response_format)
        return await self._retry_hangul_leaks(
            results, pairs, system, language_label, seed=_SEED, response_format=response_format)
```

(주의: `build_verification_checklist`의 첫 인자는 기존 코드와 동일하게 리터럴 `"대상언어"`를 유지한다 — 이 비대칭은 설계 범위 밖이라 그대로 둔다.)

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_claude_client.py tests/test_gpt_client.py -v`
Expected: PASS — 새 테스트 2개 포함, 기존 `test_correct_primary_system_prompt_contains_shared_verification_block`/`test_verify_and_refine_system_prompt_contains_shared_verification_block`도 그대로 통과(`glossary_block` 기본값이 `""`이라 기존 호출부와 동일한 결과를 내기 때문).

- [ ] **Step 6: Commit**

```bash
git add backend/app/providers/claude_client.py backend/app/providers/gpt_client.py backend/tests/test_claude_client.py backend/tests/test_gpt_client.py
git commit -m "feat: thread glossary_entries into correct_primary and verify_and_refine"
```

- [ ] **Step 7: `extract_glossary_terms`에 대한 실패하는 테스트 작성**

`backend/tests/test_gpt_client.py`에 추가(파일 안의 `gloss_words` 테스트 패턴을 그대로 따른다):

```python
@pytest.mark.asyncio
async def test_extract_glossary_terms_returns_call_result(monkeypatch):
    captured = {}

    async def fake_call(self, system, user, key=None, label=None, model_override=None):
        captured["system"] = system
        captured["user"] = user
        captured["model_override"] = model_override
        return [{"korean_term": "김현", "category": "person", "canonical": "Kim Hyun"}]

    monkeypatch.setattr(GptClient, "_call", fake_call)
    client = GptClient(api_key="x")
    profile = {"target_language": "es", "variant": "LATAM"}
    items = [{"id": "s1", "korean_text": "김현아 밥 먹었어?", "target_text": "Kim Hyun, ¿comiste?"}]

    result = await client.extract_glossary_terms(items, profile)

    assert result == [{"korean_term": "김현", "category": "person", "canonical": "Kim Hyun"}]
    assert captured["model_override"] == client._light_model
    assert "고유명사" in captured["system"]
```

- [ ] **Step 8: 테스트 실패 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_gpt_client.py::test_extract_glossary_terms_returns_call_result -v`
Expected: FAIL with `AttributeError: 'GptClient' object has no attribute 'extract_glossary_terms'`

(참고: 위 fake_call 시그니처는 실제 `GptClient._call`이 받는 키워드 인자와 맞아야 한다 — `gloss_words`가 이미 `self._call(system, user, key="results", label=..., model_override=self._light_model)` 형태로 호출하고 있으므로 그 패턴을 그대로 따른다.)

- [ ] **Step 9: `extract_glossary_terms` 구현**

`backend/app/providers/gpt_client.py`에서 `_GENDER_SWAP_SCHEMA_INSTRUCTION` 근처의 스키마 상수들 옆에 추가:

```python
_GLOSSARY_EXTRACTION_SCHEMA_INSTRUCTION = (
    '반드시 {"results": [...]} 형태의 JSON 객체만 출력하라. results 배열의 '
    "각 항목은 정확히 다음 키를 가진 JSON 객체여야 한다: "
    'korean_term (문자열, 한국어 대표형 — 이름/장소/상호/직함 등 고유명사의 '
    "기본형. 축약형·호격형이 아니라 성+이름 전체 같은 완전한 형태로), "
    'category (문자열, 반드시 다음 중 하나: "person", "place", "business", "title"), '
    "canonical (문자열, target_text에서 실제로 쓰인 이 용어의 대상언어 표기). "
    "이미 다른 회차에서 등록된 표기와 겹치는 인물이면 canonical은 그 대상언어 "
    "문장에서 실제로 쓰인 표기 그대로 적어라(추측해서 통일하지 마라 — 통일 "
    "여부 판단은 저장 단계에서 따로 한다)."
)
```

`gloss_words` 메서드 바로 다음에 추가:

```python
    async def extract_glossary_terms(self, items: List[dict], profile: dict) -> List[dict]:
        language_label = _language_label(profile)
        system = (
            f"다음은 한국어 원문(korean_text)과 그 {language_label} 번역문"
            "(target_text) 목록이다. 각 대사에서 사람 이름, 장소, 상호(가게·회사 "
            "이름), 직함/호칭 중 다른 회차·다른 언어판에서도 표기가 일관되게 "
            "유지되어야 하는 고유명사를 찾아라. 흔한 일반명사(엄마, 오빠, 사장님 "
            "같은 관계/역할 호칭 그 자체)는 특정 인물을 가리키는 고유한 이름이 "
            "아니면 뽑지 마라. 고유명사가 전혀 없는 대사는 결과에서 빼라.\n"
            + _GLOSSARY_EXTRACTION_SCHEMA_INSTRUCTION
        )
        user = json.dumps(items, ensure_ascii=False)
        return await self._call(system, user, key="results", label="작품 용어집 추출",
                                 model_override=self._light_model)
```

- [ ] **Step 10: 테스트 통과 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_gpt_client.py -v`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add backend/app/providers/gpt_client.py backend/tests/test_gpt_client.py
git commit -m "feat: add extract_glossary_terms to GptClient"
```

---

## Task 3: live.py / mock.py — ABC 계약 완성

**Files:**
- Modify: `backend/app/providers/live.py`
- Modify: `backend/app/providers/mock.py`

**Interfaces:**
- Consumes: Task 1의 ABC 시그니처(`correct_primary`/`verify_and_refine`의 `glossary_entries`, `extract_glossary_terms`), Task 2의 `GptClient.extract_glossary_terms`.
- Produces: `LiveModelProvider`/`MockProvider`가 `ModelProvider` ABC를 완전히 만족함(다른 모든 기존 테스트가 다시 인스턴스화 가능해짐).

- [ ] **Step 1: 전체 테스트 스위트로 현재 실패 재확인**

Run: `cd backend && ./venv/bin/pytest tests/ -v 2>&1 | tail -30`
Expected: 다수의 `TypeError: Can't instantiate abstract class MockProvider with abstract method extract_glossary_terms` 실패 (Task 1 Step 21에서 예견한 상태).

- [ ] **Step 2: `mock.py` 최소 구현**

`backend/app/providers/mock.py` 상단 import에 `Optional` 추가:

```python
from typing import List, Optional
```

`MockProvider.correct_primary`를 찾아 시그니처 교체:

```python
    async def correct_primary(self, pairs: List[dict], profile: dict,
                               pending_sensitive_hits: List[dict],
                               knowledge: str, format_constraint: str,
                               extra_instruction: str = "",
                               glossary_entries: Optional[List[dict]] = None) -> List[dict]:
        return _detect_corrections(pairs, pending_sensitive_hits)
```

`MockProvider.verify_and_refine`을 찾아 시그니처 교체:

```python
    async def verify_and_refine(self, pairs: List[dict], profile: dict,
                                 pending_sensitive_hits: List[dict],
                                 knowledge: str, format_constraint: str,
                                 extra_instruction: str = "",
                                 glossary_entries: Optional[List[dict]] = None) -> List[dict]:
        return _detect_corrections(pairs, pending_sensitive_hits)
```

`MockProvider.gloss_words` 바로 다음에 추가:

```python
    async def extract_glossary_terms(self, items: List[dict], profile: dict) -> List[dict]:
        return []
```

- [ ] **Step 3: `live.py` 구현**

`backend/app/providers/live.py` 상단 import에 `Optional` 추가:

```python
from typing import List, Optional
```

`LiveModelProvider.correct_primary`를 찾아 교체:

```python
    async def correct_primary(self, pairs: List[dict], profile: dict,
                               pending_sensitive_hits: List[dict],
                               knowledge: str, format_constraint: str,
                               extra_instruction: str = "",
                               glossary_entries: Optional[List[dict]] = None) -> List[dict]:
        return await self._claude.correct_primary(
            pairs, profile, pending_sensitive_hits,
            knowledge, format_constraint, extra_instruction, glossary_entries,
        )
```

`LiveModelProvider.verify_and_refine`을 찾아 교체:

```python
    async def verify_and_refine(self, pairs: List[dict], profile: dict,
                                 pending_sensitive_hits: List[dict],
                                 knowledge: str, format_constraint: str,
                                 extra_instruction: str = "",
                                 glossary_entries: Optional[List[dict]] = None) -> List[dict]:
        return await self._gpt.verify_and_refine(
            pairs, profile, pending_sensitive_hits,
            knowledge, format_constraint, extra_instruction, glossary_entries,
        )
```

`LiveModelProvider.gloss_words` 바로 다음에 추가:

```python
    async def extract_glossary_terms(self, items: List[dict], profile: dict) -> List[dict]:
        return await self._gpt.extract_glossary_terms(items, profile)
```

- [ ] **Step 4: 전체 테스트 스위트 통과 확인**

Run: `cd backend && ./venv/bin/pytest tests/ -v 2>&1 | tail -30`
Expected: PASS — Task 1에서 깨졌던 `MockProvider` 인스턴스화 실패가 모두 사라짐.

- [ ] **Step 5: Commit**

```bash
git add backend/app/providers/live.py backend/app/providers/mock.py
git commit -m "feat: complete ModelProvider ABC contract in live and mock providers"
```

---

## Task 4: pretreatment.py / knowledge/loader.py — 구 글로서리 기계 치환 제거

**Files:**
- Modify: `backend/app/core/pretreatment.py`
- Modify: `backend/app/knowledge/loader.py`
- Delete: `backend/app/knowledge/glossary.yaml`
- Modify: `backend/tests/core/test_pretreatment.py`

**Interfaces:**
- Consumes: 없음.
- Produces: `run_pretreatment(pairs, cta_patterns, profanity_entries, sensitive_terms, target_version_id) -> PretreatmentResult` (5-파라미터, `glossary_entries` 제거됨). `load_knowledge`는 변경 없음(내부 스킵 목록만 변경).

- [ ] **Step 1: 기존 글로서리 테스트 삭제, 나머지 호출부 갱신 — 실패하는 상태로 만들기**

`backend/tests/core/test_pretreatment.py`에서 `test_glossary_replaces_alias_with_canonical_name` 함수 전체를 삭제한다. 나머지 5개 테스트(`test_cta_pattern_is_removed`, `test_profanity_dictionary_entry_is_replaced`, `test_sensitive_term_not_in_profanity_dict_becomes_pending_hit`, `test_unmatched_text_is_unchanged_and_produces_no_findings`, `test_pair_without_target_is_skipped`)에서 `run_pretreatment(...)` 호출부의 글로서리 위치 인자를 제거한다. 최종 파일 내용:

```python
import pytest
from app.core.pretreatment import run_pretreatment
from app.schemas import AlignedPair, SegmentText

CTA_PATTERNS = [r"구독.{0,5}좋아요"]
PROFANITY = [{"term": "mierda", "replacement": "[삐-]"}]
SENSITIVE_TERMS = ["mierda", "pendejo"]


def _pair(text: str) -> AlignedPair:
    return AlignedPair(id="p1", target=SegmentText(start=0.0, end=1.0, text=text))


def test_cta_pattern_is_removed():
    result = run_pretreatment([_pair("구독 좋아요 눌러주세요")], CTA_PATTERNS, [], [], "tv1")
    assert "구독" not in result.pairs[0].target.text
    assert result.findings[0].category == "cta"


def test_profanity_dictionary_entry_is_replaced():
    result = run_pretreatment([_pair("qué mierda")], [], PROFANITY, SENSITIVE_TERMS, "tv1")
    assert result.pairs[0].target.text == "qué [삐-]"
    assert result.findings[0].category == "sensitivity"
    assert result.findings[0].model == "사전필터"


def test_sensitive_term_not_in_profanity_dict_becomes_pending_hit():
    result = run_pretreatment([_pair("eres un pendejo")], [], PROFANITY, SENSITIVE_TERMS, "tv1")
    assert result.pending_sensitive_hits == [{"segment_id": "p1", "term": "pendejo"}]
    assert result.findings == []


def test_unmatched_text_is_unchanged_and_produces_no_findings():
    result = run_pretreatment([_pair("hola mundo")], CTA_PATTERNS, PROFANITY, SENSITIVE_TERMS, "tv1")
    assert result.pairs[0].target.text == "hola mundo"
    assert result.findings == []
    assert result.pending_sensitive_hits == []


def test_pair_without_target_is_skipped():
    pair = AlignedPair(id="p1", target=None)
    result = run_pretreatment([pair], CTA_PATTERNS, PROFANITY, SENSITIVE_TERMS, "tv1")
    assert result.findings == []
    assert result.pending_sensitive_hits == []
```

(정확한 `CTA_PATTERNS`/`PROFANITY`/`SENSITIVE_TERMS` 값과 어서션은 기존 파일에 있던 것과 동일하게 유지한다 — 여기서는 글로서리 인자 제거와 함수 시그니처만 바뀐다. 실제 편집 시 기존 파일을 열어 이 5개 함수의 상수/어서션이 위와 다르면 위 내용이 아니라 기존 값을 그대로 보존한 채 인자 목록만 고친다.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && ./venv/bin/pytest tests/core/test_pretreatment.py -v`
Expected: FAIL — `TypeError: run_pretreatment() takes from 5 to 6 positional arguments but 5 were given`(현재 시그니처는 `pairs, glossary_entries, cta_patterns, profanity_entries, sensitive_terms, target_version_id`라 인자 개수/순서가 안 맞음)

- [ ] **Step 3: `run_pretreatment` 구현 — `_apply_glossary` 제거**

`backend/app/core/pretreatment.py`에서 `_apply_glossary` 함수 전체를 삭제한다. `run_pretreatment` 함수를 다음으로 교체(본문에서 `_apply_glossary` 호출 줄만 제거하고 나머지 로직은 기존 그대로 유지):

```python
def run_pretreatment(pairs: List[AlignedPair], cta_patterns: List[str],
                      profanity_entries: List[dict], sensitive_terms: List[str],
                      target_version_id: str) -> PretreatmentResult:
    """design §전체 파이프라인 S1: #3(뻔한 비속어)·#6(CTA)을 LLM 없이 먼저
    처리한다. 고유명사 표기 통일(#4, 구 glossary.yaml 기계적 치환)은 같은
    성씨를 쓰는 다른 인물을 구분 못 해 폐기됐다 — 이제는 S2 검증 프롬프트에
    [작품 용어집]을 주입해 LLM이 문맥을 보고 판단한다(category: "glossary").
    profanity_entries에 없는 민감어 후보(sensitive_terms 매칭)는 애매한
    경우로 보고 Claude 1차로 넘긴다(pending_sensitive_hits)."""
    findings: List[Finding] = []

    for pair in pairs:
        if pair.target is None:
            continue
        original = pair.target.text
        text = original

        text, cta_hits = _apply_cta_patterns(text, cta_patterns)
        text, profanity_hits = _apply_profanity_dictionary(text, profanity_entries)

        if text != original:
            pair.target.text = text
            if cta_hits:
                findings.append(_make_finding(
                    target_version_id, pair.id, "cta",
                    "구독/좋아요 등 홍보 문구 삭제", original, text))
            if profanity_hits:
                findings.append(_make_finding(
                    target_version_id, pair.id, "sensitivity",
                    f"사전 등록된 비속어 자동 교정: {', '.join(profanity_hits)}", original, text))

    pending_sensitive_hits = find_pending_sensitive_hits(pairs, sensitive_terms, profanity_entries)

    return PretreatmentResult(pairs=pairs, findings=findings,
                               pending_sensitive_hits=pending_sensitive_hits)
```

(주의: 이 단계에서 실제로 편집할 때는 기존 `_apply_cta_patterns`/`_apply_profanity_dictionary`/`_make_finding`/`find_pending_sensitive_hits`의 정확한 호출 인자 순서를 현재 파일에서 그대로 유지해야 한다 — 여기서는 `_apply_glossary` 호출과 그 결과 병합 로직만 제거하는 것이 유일한 실질 변경이다.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && ./venv/bin/pytest tests/core/test_pretreatment.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: `knowledge/loader.py`에서 `load_glossary` 제거**

`backend/app/knowledge/loader.py`에서 `load_glossary` 함수 전체를 삭제한다. `load_knowledge` 함수 안의 스킵 목록에서 `"glossary.yaml"`을 제거한다(다른 스킵 대상인 `"sensitive_terms.yaml"`, `"cta_patterns.yaml"`, `"profanity_dictionary.yaml"`은 그대로 둔다).

Run: `cd backend && ./venv/bin/pytest tests/test_knowledge.py -v`
Expected: PASS(만약 `test_knowledge.py`에 `load_glossary`를 직접 테스트하는 함수가 있다면 그 테스트도 삭제한다 — `grep -n "load_glossary" tests/test_knowledge.py`로 먼저 확인)

- [ ] **Step 6: `glossary.yaml` 삭제**

```bash
git rm backend/app/knowledge/glossary.yaml
```

- [ ] **Step 7: pipeline.py가 아직 옛 시그니처로 호출 중이라 실패하는지 확인(예상된 실패)**

Run: `cd backend && ./venv/bin/pytest tests/core/test_pipeline.py -v 2>&1 | tail -20`
Expected: FAIL — `pipeline.py`가 여전히 `load_glossary()`를 호출하고 `run_pretreatment`를 옛 6-인자로 호출하고 있어 `NameError` 또는 `TypeError`가 난다. 이건 Task 6에서 고친다(지금은 실패가 정상이다).

- [ ] **Step 8: Commit**

```bash
git add backend/app/core/pretreatment.py backend/app/knowledge/loader.py backend/tests/core/test_pretreatment.py
git commit -m "refactor: remove mechanical glossary substitution from pretreatment"
```

---

## Task 5: models.py + Alembic migration — glossary_entries / glossary_spellings 테이블

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/alembic/versions/615c994a801e_add_glossary_tables.py`

**Interfaces:**
- Consumes: 없음.
- Produces: `GlossaryEntry(id, title_id, korean_term, category, aliases)`, `GlossarySpelling(id, entry_id, language, variant, canonical)` ORM 클래스 — Task 6의 `repositories.py`가 임포트해서 쓴다.

- [ ] **Step 1: 모델 존재를 확인하는 실패하는 테스트 작성**

`backend/tests/test_models.py`에 추가(기존 `CharacterGenderFact` 관련 테스트 패턴을 따른다 — 파일을 열어 유사 테스트가 있으면 그 스타일을 그대로 따르고, 없으면 아래처럼 작성):

```python
def test_glossary_entry_and_spelling_have_expected_columns():
    from app.models import GlossaryEntry, GlossarySpelling

    entry = GlossaryEntry(title_id="t1", korean_term="김현", category="person", aliases=["짱구"])
    assert entry.title_id == "t1"
    assert entry.korean_term == "김현"
    assert entry.category == "person"
    assert entry.aliases == ["짱구"]

    spelling = GlossarySpelling(entry_id="e1", language="es", variant="LATAM", canonical="Kim Hyun")
    assert spelling.entry_id == "e1"
    assert spelling.language == "es"
    assert spelling.variant == "LATAM"
    assert spelling.canonical == "Kim Hyun"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_models.py::test_glossary_entry_and_spelling_have_expected_columns -v`
Expected: FAIL with `ImportError: cannot import name 'GlossaryEntry'`

- [ ] **Step 3: ORM 클래스 구현**

`backend/app/models.py`의 `CharacterGenderFact` 클래스 바로 다음에 추가:

```python
class GlossaryEntry(Base):
    __tablename__ = "glossary_entries"
    __table_args__ = (UniqueConstraint("title_id", "korean_term"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    title_id: Mapped[str] = mapped_column(ForeignKey("titles.id"))
    korean_term: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)  # "person" | "place" | "business" | "title"
    # 규칙으로 자동 유도되지 않는 별명만 담는다(예: 김현의 별명 "짱구") —
    # 축약형·호격형(예: 현, 현아)은 프롬프트 지침으로 이미 같은 대상으로
    # 처리되므로 여기 저장하지 않는다.
    aliases: Mapped[list] = mapped_column(JSON, default=list)


class GlossarySpelling(Base):
    __tablename__ = "glossary_spellings"
    __table_args__ = (UniqueConstraint("entry_id", "language", "variant"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    entry_id: Mapped[str] = mapped_column(ForeignKey("glossary_entries.id"))
    language: Mapped[str] = mapped_column(String)
    variant: Mapped[str] = mapped_column(String)
    # 그 언어판의 확정 표기. 행이 없으면 그 언어판엔 아직 등록되지 않았다는
    # 뜻 — 자동 추출은 이 행이 이미 있으면 절대 덮어쓰지 않는다("최초 확정
    # 우선", 사람의 PATCH만 덮어쓸 수 있다).
    canonical: Mapped[str] = mapped_column(String)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Alembic 마이그레이션 작성**

`backend/alembic/versions/615c994a801e_add_glossary_tables.py` (신규 파일):

```python
"""add glossary_entries and glossary_spellings

Revision ID: 615c994a801e
Revises: e2b6a5c1f908
Create Date: 2026-09-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '615c994a801e'
down_revision: Union[str, Sequence[str], None] = 'e2b6a5c1f908'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'glossary_entries',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('title_id', sa.String(), sa.ForeignKey('titles.id'), nullable=False),
        sa.Column('korean_term', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('aliases', sa.JSON(), nullable=False),
        sa.UniqueConstraint('title_id', 'korean_term'),
    )
    op.create_table(
        'glossary_spellings',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('entry_id', sa.String(), sa.ForeignKey('glossary_entries.id'), nullable=False),
        sa.Column('language', sa.String(), nullable=False),
        sa.Column('variant', sa.String(), nullable=False),
        sa.Column('canonical', sa.String(), nullable=False),
        sa.UniqueConstraint('entry_id', 'language', 'variant'),
    )


def downgrade() -> None:
    op.drop_table('glossary_spellings')
    op.drop_table('glossary_entries')
```

- [ ] **Step 6: 마이그레이션이 head까지 깨끗하게 적용되는지 확인**

Run: `cd backend && ./venv/bin/alembic upgrade head && ./venv/bin/alembic heads`
Expected: 에러 없이 완료, `615c994a801e (head)` 출력.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/tests/test_models.py backend/alembic/versions/615c994a801e_add_glossary_tables.py
git commit -m "feat: add glossary_entries and glossary_spellings tables"
```

---

## Task 6: repositories.py — 용어집 조회/upsert/CRUD

**Files:**
- Modify: `backend/app/repositories.py`
- Modify: `backend/tests/test_repositories.py`

**Interfaces:**
- Consumes: `GlossaryEntry`, `GlossarySpelling` (Task 5). `run_pretreatment`의 새 5-파라미터 시그니처 (Task 4, 회귀 테스트 재작성용).
- Produces: `get_glossary_prompt_entries(session, title_id, language, variant) -> list[dict]`, `upsert_glossary_extraction(session, title_id, language, variant, extractions: list[dict]) -> None`, `create_glossary_entry(session, title_id, korean_term, category, aliases) -> GlossaryEntry`, `update_glossary_entry(session, entry_id, category=None, aliases=None, spellings=None) -> GlossaryEntry | None`, `delete_glossary_entry(session, entry_id) -> bool` — Task 8(background.py)과 Task 9(routers)가 이 함수들을 쓴다.

- [ ] **Step 1: 기존 회귀 테스트를 CTA 기반으로 재작성 — 실패하는 상태로 만들기**

`backend/tests/test_repositories.py`에서 `test_save_pipeline_result_persists_final_text_and_status_for_pretreatment_findings` 함수 전체를 다음으로 교체:

```python
@pytest.mark.asyncio
async def test_save_pipeline_result_persists_final_text_and_status_for_pretreatment_findings():
    """회귀(important): FindingRow(...) 생성에서 final_text/reviewer_name을
    빠뜨리면, pretreatment.py/safety_net.py가 status="approved",
    final_text=suggested_text로 직접 구성해 넘긴 Finding이 DB에는
    final_text=""(기본값)로 저장된다 — 검수자 판단 없이 이미 확정된 자동교정
    결과가 검수 화면에서 빈 텍스트로 보이는 버그였다. run_pretreatment를 실제
    CTA 패턴으로 실행해 진짜 Finding을 만들고 save_pipeline_result에 그대로
    흘려보내 영속화된 행을 검증한다."""
    from app.core.pretreatment import run_pretreatment
    from app.schemas import AlignedPair, SegmentText

    async with async_session() as session:
        title = Title(name="Movie E", type="movie", created_at=datetime.now())
        session.add(title)
        await session.flush()
        tv = await _make_target_version(session, title)

        pairs = [AlignedPair(id="pair_1",
                              target=SegmentText(start=0.0, end=1.5, text="구독 좋아요 눌러주세요"))]
        pretreatment = run_pretreatment(pairs, [r"구독.{0,5}좋아요"], [], [], tv.id)
        assert pretreatment.findings, "CTA 패턴이 실제로 Finding을 만들어야 이 테스트가 유효하다"

        result = _result_with(findings=pretreatment.findings, pairs=pretreatment.pairs)
        await save_pipeline_result(session, tv.id, result)
        await session.commit()

        rows = await get_findings(session, tv.id)
        assert len(rows) == 1
        row = rows[0]
        assert row.category == "cta"
        assert row.status == "approved"
        assert row.suggested_text == "눌러주세요"
        assert row.final_text == "눌러주세요"
```

(`_make_target_version`, `_result_with`, `get_findings`, `save_pipeline_result`, `Title` 임포트 등은 파일에 이미 있는 헬퍼를 그대로 사용한다 — 이름이 다르면 기존 파일의 실제 헬퍼 이름으로 맞춘다.)

같은 파일 상단 import에 추가:

```python
from app.repositories import get_glossary_prompt_entries, upsert_glossary_extraction
```

파일 끝에 새 테스트 추가:

```python
@pytest.mark.asyncio
async def test_upsert_glossary_extraction_never_overwrites_existing_spelling():
    """회귀 방지: 자동 추출은 이미 등록된 표기를 절대 덮어쓰지 않는다("최초
    확정 우선") — 사람이 PATCH로 직접 고치는 것과 다른 경로다."""
    async with async_session() as session:
        title = Title(name="Test Drama", type="series", created_at=datetime.now())
        session.add(title)
        await session.flush()
        title_id = title.id
        await upsert_glossary_extraction(
            session, title_id, "es", "LATAM",
            [{"korean_term": "김현", "category": "person", "canonical": "Kim Hyun"}])
        await session.commit()

    async with async_session() as session:
        await upsert_glossary_extraction(
            session, title_id, "es", "LATAM",
            [{"korean_term": "김현", "category": "person", "canonical": "Kim Hyeon"}])
        await session.commit()

    async with async_session() as session:
        entries = await get_glossary_prompt_entries(session, title_id, "es", "LATAM")
        assert len(entries) == 1
        assert entries[0]["canonical"] == "Kim Hyun"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_repositories.py::test_save_pipeline_result_persists_final_text_and_status_for_pretreatment_findings tests/test_repositories.py::test_upsert_glossary_extraction_never_overwrites_existing_spelling -v`
Expected: FAIL — `ImportError: cannot import name 'get_glossary_prompt_entries'`

- [ ] **Step 3: repositories.py 구현**

`backend/app/repositories.py` 상단 import를 찾아 `GlossaryEntry`, `GlossarySpelling` 추가:

```python
from app.models import (
    FindingRow, Segment, SttCorrection, CharacterGenderFact, TargetVersion,
    GlossaryEntry, GlossarySpelling,
)
```

파일 끝에 추가:

```python
def _language_variant_key(language: str, variant: str) -> str:
    return f"{language}_{variant}"


async def get_glossary_prompt_entries(session: AsyncSession, title_id: str,
                                       language: str, variant: str) -> list:
    """검증 프롬프트에 주입할 [작품 용어집] 항목만 돌려준다 — 이 (language,
    variant)에 대해 이미 GlossarySpelling이 등록된 항목만 포함한다(아직
    등록 안 된 언어판은 뭐라고 부를지 모르므로 프롬프트에 넣을 수 없다).
    build_glossary_block이 바로 쓸 수 있는 평평한 dict 리스트로 반환한다."""
    rows = (await session.execute(
        select(GlossaryEntry.korean_term, GlossaryEntry.category, GlossaryEntry.aliases,
               GlossarySpelling.canonical)
        .join(GlossarySpelling, GlossarySpelling.entry_id == GlossaryEntry.id)
        .where(
            GlossaryEntry.title_id == title_id,
            GlossarySpelling.language == language,
            GlossarySpelling.variant == variant,
        )
    )).all()
    return [
        {"korean_term": korean_term, "category": category, "aliases": aliases, "canonical": canonical}
        for korean_term, category, aliases, canonical in rows
    ]


async def upsert_glossary_extraction(session: AsyncSession, title_id: str,
                                      language: str, variant: str, extractions: list) -> None:
    """자동 추출된 후보(extract_glossary_terms의 결과)를 title 단위로
    upsert한다. (title_id, korean_term)이 이미 있으면 새 GlossaryEntry를 또
    만들지 않고 재사용한다. GlossarySpelling은 (entry_id, language, variant)에
    이미 행이 있으면 절대 덮어쓰지 않는다 — "최초 확정 우선"(design §잔존
    리스크: 첫 등록 표기가 이후 재분석에서도 계속 쓰인다). 사람이 PATCH
    /glossary/{entry_id}로 직접 고치는 것과 다른 경로다."""
    if not extractions:
        return
    existing_entries = (await session.execute(
        select(GlossaryEntry).where(GlossaryEntry.title_id == title_id)
    )).scalars().all()
    entry_by_term = {e.korean_term: e for e in existing_entries}

    for extraction in extractions:
        term = extraction["korean_term"]
        entry = entry_by_term.get(term)
        if entry is None:
            entry = GlossaryEntry(title_id=title_id, korean_term=term,
                                   category=extraction["category"], aliases=[])
            session.add(entry)
            await session.flush()
            entry_by_term[term] = entry

        existing_spelling = (await session.execute(
            select(GlossarySpelling).where(
                GlossarySpelling.entry_id == entry.id,
                GlossarySpelling.language == language,
                GlossarySpelling.variant == variant,
            )
        )).scalar_one_or_none()
        if existing_spelling is None:
            session.add(GlossarySpelling(
                entry_id=entry.id, language=language, variant=variant,
                canonical=extraction["canonical"],
            ))
    await session.flush()


async def create_glossary_entry(session: AsyncSession, title_id: str, korean_term: str,
                                 category: str, aliases: list) -> GlossaryEntry:
    entry = GlossaryEntry(title_id=title_id, korean_term=korean_term,
                           category=category, aliases=aliases)
    session.add(entry)
    await session.flush()
    return entry


async def update_glossary_entry(session: AsyncSession, entry_id: str,
                                 category: str = None, aliases: list = None,
                                 spellings: dict = None):
    """부분 수정(머지) — category/aliases 또는 spellings 중 온 것만 바꾼다.
    spellings는 {"{language}_{variant}": canonical} 형태다(TitleArchiveList가
    target_version을 구분할 때 쓰는 것과 같은 키 형식). 사람이 직접 고치는
    경로이므로(자동 추출과 달리) 기존 표기를 그대로 덮어쓴다."""
    entry = await session.get(GlossaryEntry, entry_id)
    if entry is None:
        return None
    if category is not None:
        entry.category = category
    if aliases is not None:
        entry.aliases = aliases
    if spellings is not None:
        existing = (await session.execute(
            select(GlossarySpelling).where(GlossarySpelling.entry_id == entry_id)
        )).scalars().all()
        existing_by_key = {
            _language_variant_key(s.language, s.variant): s for s in existing
        }
        for key, canonical in spellings.items():
            language, variant = key.split("_", 1)
            spelling = existing_by_key.get(key)
            if spelling is not None:
                spelling.canonical = canonical
            else:
                session.add(GlossarySpelling(
                    entry_id=entry_id, language=language, variant=variant, canonical=canonical))
    await session.flush()
    return entry


async def delete_glossary_entry(session: AsyncSession, entry_id: str) -> bool:
    entry = await session.get(GlossaryEntry, entry_id)
    if entry is None:
        return False
    await session.execute(delete(GlossarySpelling).where(GlossarySpelling.entry_id == entry_id))
    await session.delete(entry)
    await session.flush()
    return True
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_repositories.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories.py backend/tests/test_repositories.py
git commit -m "feat: add glossary repository functions"
```

---

## Task 7: pipeline.py — 파이프라인 배선(주입 + 추출 패스)

**Files:**
- Modify: `backend/app/core/pipeline.py`

**Interfaces:**
- Consumes: `Optional` (이미 임포트됨), Task 4의 새 `run_pretreatment` 시그니처, Task 1의 `ModelProvider.extract_glossary_terms`, `_safe_call(coro, label, note, target_version_id, warnings) -> list` (기존), `_split_into_scenes`(변경 없음, 재사용하지 않음 — 추출은 단일 호출).
- Produces: `run_pipeline_phase2(pairs, provider, profile, knowledge, pending_sensitive_hits, target_version_id, resolved_registers, glossary_entries=None) -> dict`(반환 dict에 `"glossary_extractions": list[dict]` 키 추가). `_extract_glossary_terms_pass(pairs, provider, profile, target_version_id, warnings) -> list[dict]` — Task 8이 `phase2` 반환값의 `glossary_extractions`를 사용한다.

- [ ] **Step 1: `load_glossary` 제거 및 `run_pretreatment` 호출부 갱신**

`backend/app/core/pipeline.py` 상단 import 줄에서 `load_glossary`를 제거한다:

```python
from app.knowledge.loader import load_knowledge, load_sensitive_terms, load_cta_patterns
```

(정확한 원래 import 줄의 나머지 이름들은 그대로 유지하고 `load_glossary`만 제거한다.)

`glossary = load_glossary()` 줄을 삭제한다.

`run_pretreatment(...)` 호출부를 찾아 인자 목록에서 `glossary`를 제거:

```python
    pretreatment = run_pretreatment(
        pairs, cta_patterns, profanity_dictionary, sensitive_terms,
        target_version_id,
    )
```

- [ ] **Step 2: 확인 — phase1 테스트가 다시 통과하는지**

Run: `cd backend && ./venv/bin/pytest tests/core/test_pipeline.py -v -k "phase1 or pretreatment"`
Expected: PASS (Task 4 Step 7에서 확인했던 실패가 여기서 해소된다)

- [ ] **Step 3: `_extract_glossary_terms_pass`에 대한 실패하는 테스트 작성**

`backend/tests/core/test_pipeline.py`에 추가(파일에 이미 있는 `MockProvider`/`asyncio.run` 기반 테스트 패턴을 따른다):

```python
def test_extract_glossary_terms_pass_calls_provider_with_filtered_pairs():
    from app.core.pipeline import _extract_glossary_terms_pass
    from app.providers.mock import MockProvider
    from app.schemas import AlignedPair, SegmentText

    captured = {}

    class SpyProvider(MockProvider):
        async def extract_glossary_terms(self, items, profile):
            captured["items"] = items
            return [{"korean_term": "김현", "category": "person", "canonical": "Kim Hyun"}]

    pairs = [
        AlignedPair(id="p1",
                    korean=SegmentText(start=0.0, end=1.0, text="김현아 밥 먹었어?"),
                    target=SegmentText(start=0.0, end=1.0, text="Kim Hyun, comiste?")),
        AlignedPair(id="p2", korean=None,
                    target=SegmentText(start=1.0, end=2.0, text="no korean pair")),
    ]
    warnings = []

    result = asyncio.run(_extract_glossary_terms_pass(
        pairs, SpyProvider(), {"target_language": "es", "variant": "LATAM"}, "tv1", warnings))

    assert result == [{"korean_term": "김현", "category": "person", "canonical": "Kim Hyun"}]
    assert captured["items"] == [{"id": "p1", "korean_text": "김현아 밥 먹었어?",
                                   "target_text": "Kim Hyun, comiste?"}]
    assert warnings == []
```

(`asyncio`가 파일 상단에 이미 임포트되어 있지 않다면 `import asyncio`를 추가한다.)

- [ ] **Step 4: 테스트 실패 확인**

Run: `cd backend && ./venv/bin/pytest tests/core/test_pipeline.py::test_extract_glossary_terms_pass_calls_provider_with_filtered_pairs -v`
Expected: FAIL with `ImportError: cannot import name '_extract_glossary_terms_pass'`

- [ ] **Step 5: `_extract_glossary_terms_pass` 구현**

`backend/app/core/pipeline.py`의 `_safe_call` 함수 바로 다음에 추가:

```python
async def _extract_glossary_terms_pass(
    pairs: list, provider: ModelProvider, profile: dict,
    target_version_id: str, warnings: list,
) -> list:
    """정렬된 (korean_text, target_text) 쌍에서 작품 용어집 후보(고유명사)를
    한 번의 LLM 호출로 추출한다. 한국어 원문이 없는 반쪽짜리 pair는 비교
    기준이 없어 제외한다(_run_dual_verification_pass의 filtered_pairs와
    동일한 필터)."""
    filtered_pairs = [p for p in pairs if p.target is not None and p.korean is not None]
    if not filtered_pairs:
        return []
    items = [
        {"id": p.id, "korean_text": p.korean.text, "target_text": p.target.text}
        for p in filtered_pairs
    ]
    return await _safe_call(
        provider.extract_glossary_terms(items, profile),
        "작품 용어집 추출", "이번 회차에서는 새 용어를 추출하지 못했습니다",
        target_version_id, warnings)
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd backend && ./venv/bin/pytest tests/core/test_pipeline.py::test_extract_glossary_terms_pass_calls_provider_with_filtered_pairs -v`
Expected: PASS

- [ ] **Step 7: `_run_dual_verification_pass`/`run_pipeline_phase2`에 `glossary_entries` 스레딩 — 실패하는 테스트 작성**

`backend/tests/core/test_pipeline.py`에 추가:

```python
def test_run_pipeline_phase2_passes_glossary_entries_to_correct_primary_and_returns_extractions():
    from app.core.pipeline import run_pipeline_phase2
    from app.providers.mock import MockProvider
    from app.schemas import AlignedPair, SegmentText

    captured = {}

    class SpyProvider(MockProvider):
        async def correct_primary(self, pairs, profile, pending_sensitive_hits,
                                   knowledge, format_constraint, extra_instruction="",
                                   glossary_entries=None):
            captured["correct_primary_glossary_entries"] = glossary_entries
            return []

        async def extract_glossary_terms(self, items, profile):
            return [{"korean_term": "김현", "category": "person", "canonical": "Kim Hyun"}]

    pairs = [
        AlignedPair(id="p1",
                    korean=SegmentText(start=0.0, end=1.0, text="김현아 밥 먹었어?"),
                    target=SegmentText(start=0.0, end=1.0, text="Kim Hyun, comiste?")),
    ]
    glossary_entries = [{"korean_term": "김현", "category": "person",
                          "canonical": "Kim Hyun", "aliases": []}]

    result = asyncio.run(run_pipeline_phase2(
        pairs, SpyProvider(), {"target_language": "es", "variant": "LATAM"},
        {}, [], "tv1", {}, glossary_entries,
    ))

    assert captured["correct_primary_glossary_entries"] == glossary_entries
    assert result["glossary_extractions"] == [
        {"korean_term": "김현", "category": "person", "canonical": "Kim Hyun"}]
```

- [ ] **Step 8: 테스트 실패 확인**

Run: `cd backend && ./venv/bin/pytest tests/core/test_pipeline.py::test_run_pipeline_phase2_passes_glossary_entries_to_correct_primary_and_returns_extractions -v`
Expected: FAIL — `TypeError: run_pipeline_phase2() takes ... but 8 were given` (glossary_entries 파라미터가 아직 없음)

- [ ] **Step 9: `_run_dual_verification_pass`/`run_pipeline_phase2` 구현**

`_run_dual_verification_pass` 함수 시그니처를 찾아 마지막에 파라미터 추가:

```python
async def _run_dual_verification_pass(
    pairs: list, provider: ModelProvider, profile: dict,
    pending_sensitive_hits: list, knowledge: dict,
    format_constraint: str, target_version_id: str, resolved_registers: dict,
    glossary_entries: Optional[list] = None,
) -> tuple:
```

그 함수 안의 `_verify_chunk` 내부 함수에서 `provider.correct_primary(...)`/`provider.verify_and_refine(...)` 호출부를 찾아 각각 `glossary_entries=glossary_entries` 키워드 인자를 추가:

```python
    async def _verify_chunk(chunk: list) -> tuple:
        chunk_dicts = [_to_dict(p) for p in chunk]
        return await asyncio.gather(
            _safe_call(
                provider.correct_primary(
                    chunk_dicts, profile, pending_sensitive_hits,
                    knowledge, format_constraint, glossary_entries=glossary_entries),
                "Claude 검증", "해당 구간을 스킵하고 계속 진행", target_version_id, warnings),
            _safe_call(
                provider.verify_and_refine(
                    chunk_dicts, profile, pending_sensitive_hits,
                    knowledge, format_constraint, glossary_entries=glossary_entries),
                "GPT 검증", "해당 구간을 스킵하고 계속 진행", target_version_id, warnings),
        )
```

(정확한 기존 호출부에 `extra_instruction` 등 다른 키워드 인자가 이미 있다면 그건 그대로 유지하고 `glossary_entries=glossary_entries`만 추가한다.)

`run_pipeline_phase2` 함수 시그니처를 찾아 마지막에 파라미터 추가:

```python
async def run_pipeline_phase2(pairs: list, provider: ModelProvider, profile: dict,
                               knowledge: dict, pending_sensitive_hits: list,
                               target_version_id: str, resolved_registers: dict,
                               glossary_entries: Optional[list] = None) -> dict:
```

함수 본문에서 `_run_dual_verification_pass(...)` 호출부를 찾아 `glossary_entries` 전달:

```python
    dual_verification_findings, dual_verification_warnings = await _run_dual_verification_pass(
        pairs, provider, profile,
        pending_sensitive_hits, knowledge, format_constraint,
        target_version_id, resolved_registers, glossary_entries,
    )
```

함수의 `return {...}` 직전에 추출 패스 호출 추가, 반환 dict에 `"glossary_extractions"` 키 추가:

```python
    glossary_extractions = await _extract_glossary_terms_pass(
        pairs, provider, profile, target_version_id, dual_verification_warnings)

    return {
        "pairs": pairs,
        "format_violations": final_ellipsis_violations,
        "warnings": dual_verification_warnings,
        "findings": dual_verification_findings + safety_net_findings,
        "glossary_extractions": glossary_extractions,
    }
```

(기존 반환 dict의 정확한 키 이름/값이 위와 다르면 — 예를 들어 `"format_violations"`/`"safety_net_findings"` 변수명이 실제로 다르면 — 기존 이름을 그대로 유지하고 `"glossary_extractions"` 키만 추가한다.)

- [ ] **Step 10: 테스트 통과 확인**

Run: `cd backend && ./venv/bin/pytest tests/core/test_pipeline.py -v`
Expected: PASS — 새 테스트 2개 포함, 기존 `_run_dual_verification_pass`/`run_pipeline_phase2` 호출부 테스트(위치 인자 호출)도 `glossary_entries` 기본값(`None`) 덕분에 그대로 통과.

- [ ] **Step 11: Commit**

```bash
git add backend/app/core/pipeline.py backend/tests/core/test_pipeline.py
git commit -m "feat: wire glossary injection and extraction into pipeline phase2"
```

---

## Task 8: background.py — 주입 조회 + 추출 결과 저장 배선

**Files:**
- Modify: `backend/app/background.py`

**Interfaces:**
- Consumes: `get_glossary_prompt_entries`, `upsert_glossary_extraction` (Task 6), `run_pipeline_phase2(..., glossary_entries=None)` (Task 7).
- Produces: `_run_phase2_and_save`가 분석 시작 시 title+language 용어집을 주입하고, 분석 끝에 추출 결과를 저장함 — 이후 Task 9(API)를 통해 사람이 눈으로 확인 가능해진다.

- [ ] **Step 1: 주입·저장이 실제로 일어나는지 확인하는 실패하는 테스트 작성**

`backend/tests/test_background.py`에 추가(파일에 이미 있는 `_run_phase2_and_save` 관련 테스트의 셋업 패턴 — `Title`/`Episode`/`TargetVersion`/`Segment` 생성 및 `MockProvider` 주입 방식을 그대로 따른다):

```python
@pytest.mark.asyncio
async def test_run_phase2_and_save_injects_and_persists_glossary(monkeypatch):
    from app.repositories import get_glossary_prompt_entries, upsert_glossary_extraction
    from app.providers.mock import MockProvider
    from app.background import _run_phase2_and_save

    captured = {}

    class SpyProvider(MockProvider):
        async def correct_primary(self, pairs, profile, pending_sensitive_hits,
                                   knowledge, format_constraint, extra_instruction="",
                                   glossary_entries=None):
            captured["glossary_entries"] = glossary_entries
            return []

        async def extract_glossary_terms(self, items, profile):
            return [{"korean_term": "설악산", "category": "place", "canonical": "Mount Seorak"}]

    async with async_session() as session:
        title = Title(name="T", type="series", created_at=datetime.now())
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, episode_no=1, video_path="/x.mp4")
        session.add(episode)
        await session.flush()
        tv = TargetVersion(episode_id=episode.id, target_language="es", variant="LATAM",
                            status="analyzing")
        session.add(tv)
        await session.flush()
        session.add(Segment(target_version_id=tv.id, index=0, start=0.0, end=1.0,
                             korean_text="설악산에 가자", target_text="Vamos a Seorak"))
        await session.commit()
        title_id, tv_id = title.id, tv.id

        await upsert_glossary_extraction(session, title_id, "es", "LATAM",
                                          [{"korean_term": "설악산", "category": "place",
                                            "canonical": "Mount Seorak (existing)"}])
        await session.commit()

    await _run_phase2_and_save(tv_id, SpyProvider())

    assert captured["glossary_entries"] == [
        {"korean_term": "설악산", "category": "place", "aliases": [],
         "canonical": "Mount Seorak (existing)"}]

    async with async_session() as session:
        entries = await get_glossary_prompt_entries(session, title_id, "es", "LATAM")
    assert len(entries) == 1
    assert entries[0]["canonical"] == "Mount Seorak (existing)"
```

(정확한 임포트 — `Title`, `Episode`, `TargetVersion`, `Segment`, `datetime`, `async_session` — 는 `test_background.py` 상단에 이미 있는 것을 재사용한다.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_background.py::test_run_phase2_and_save_injects_and_persists_glossary -v`
Expected: FAIL — `captured`에 `"glossary_entries"` 키가 없거나(주입 안 됨) 값이 `None`(아직 `run_pipeline_phase2`에 안 넘기고 있음).

- [ ] **Step 3: `_run_phase2_and_save` 구현**

`backend/app/background.py` 상단 import에 추가:

```python
from app.repositories import get_glossary_prompt_entries, upsert_glossary_extraction
```

`_run_phase2_and_save` 함수를 찾아, `tv = await session.get(TargetVersion, target_version_id)` 로드 직후에 `Episode` 로드와 용어집 조회를 추가:

```python
    try:
        async with async_session() as session:
            tv = await session.get(TargetVersion, target_version_id)
            episode = await session.get(Episode, tv.episode_id)
            segments = (await session.execute(
                select(Segment).where(Segment.target_version_id == target_version_id)
                .order_by(Segment.index)
            )).scalars().all()
            glossary_entries = await get_glossary_prompt_entries(
                session, episode.title_id, tv.target_language, tv.variant)
```

(`segments` 조회 줄이 기존 코드에서 이미 있다면 그 위치와 쿼리는 그대로 두고, `episode`/`glossary_entries` 두 줄만 추가한다.)

`run_pipeline_phase2(...)` 호출부를 찾아 `glossary_entries` 전달:

```python
        phase2 = await asyncio.wait_for(
            run_pipeline_phase2(
                pairs, provider, profile, knowledge, pending_sensitive_hits,
                target_version_id, resolved_registers, glossary_entries,
            ),
            timeout=ANALYSIS_TIMEOUT_SECONDS,
        )
```

`save_phase2_result(...)` 호출 직후(같은 `async with async_session() as session:` 블록 안, `await session.commit()` 이전)에 저장 호출 추가:

```python
        async with async_session() as session:
            await save_phase2_result(session, target_version_id, phase2)
            await upsert_glossary_extraction(
                session, episode.title_id, tv.target_language, tv.variant,
                phase2.get("glossary_extractions", []))
            ...
            await session.commit()
```

(`...` 부분은 기존 코드에 있던 `tv.status = "review"` 등 나머지 로직을 그대로 유지 — `episode`/`tv.target_language`/`tv.variant`는 위에서 이미 구했으므로 재사용한다.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_background.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/background.py backend/tests/test_background.py
git commit -m "feat: inject and persist glossary in phase2 background task"
```

---

## Task 9: routers/titles.py — 용어집 API

**Files:**
- Modify: `backend/app/routers/titles.py`
- Modify: `backend/tests/test_api_titles.py`

**Interfaces:**
- Consumes: `create_glossary_entry`, `update_glossary_entry`, `delete_glossary_entry` (Task 6), `GlossaryEntry`, `GlossarySpelling` (Task 5).
- Produces: `GET /titles` 응답에 `"glossary"` 필드, `POST /titles/{title_id}/glossary`, `PATCH /glossary/{entry_id}`, `DELETE /glossary/{entry_id}` — Task 10(프론트)이 이 API들을 호출한다. 응답 형태: `{"id", "korean_term", "category", "aliases", "spellings": {"{language}_{variant}": canonical}}`.

- [ ] **Step 1: `GET /titles`가 용어집을 포함하는지 실패하는 테스트 작성**

`backend/tests/test_api_titles.py`에 추가(기존 `test_list_titles_includes_character_gender_facts` 테스트의 정확한 구조를 그대로 따른다):

```python
@pytest.mark.asyncio
async def test_list_titles_includes_glossary():
    from app.models import GlossaryEntry, GlossarySpelling

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_res = await client.post("/titles", json={"name": "T", "type": "series"})
        title_id = title_res.json()["id"]

    async with async_session() as session:
        entry = GlossaryEntry(title_id=title_id, korean_term="김현", category="person", aliases=[])
        session.add(entry)
        await session.flush()
        session.add(GlossarySpelling(entry_id=entry.id, language="es", variant="LATAM",
                                      canonical="Kim Hyun"))
        await session.commit()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/titles")
    title = next(t for t in r.json() if t["id"] == title_id)
    assert len(title["glossary"]) == 1
    assert title["glossary"][0]["korean_term"] == "김현"
    assert title["glossary"][0]["spellings"] == {"es_LATAM": "Kim Hyun"}


@pytest.mark.asyncio
async def test_create_glossary_entry_and_patch_spelling():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_res = await client.post("/titles", json={"name": "T", "type": "series"})
        title_id = title_res.json()["id"]

        r = await client.post(f"/titles/{title_id}/glossary",
                               json={"korean_term": "김현", "category": "person", "aliases": []})
        assert r.status_code == 200
        entry_id = r.json()["id"]

        r = await client.patch(f"/glossary/{entry_id}",
                                json={"spellings": {"es_LATAM": "Kim Hyun"}})
        assert r.status_code == 200
        assert r.json()["spellings"] == {"es_LATAM": "Kim Hyun"}

        listed = await client.get("/titles")
    title = next(t for t in listed.json() if t["id"] == title_id)
    assert title["glossary"][0]["spellings"] == {"es_LATAM": "Kim Hyun"}


@pytest.mark.asyncio
async def test_patch_glossary_entry_overwrites_existing_spelling():
    """사람이 직접 고치는 PATCH는 자동 추출과 달리 기존 표기를 덮어써야
    한다 — upsert_glossary_extraction의 '최초 확정 우선'과 대비되는
    경로다."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_res = await client.post("/titles", json={"name": "T", "type": "series"})
        title_id = title_res.json()["id"]
        entry_res = await client.post(f"/titles/{title_id}/glossary",
                                       json={"korean_term": "김현", "category": "person", "aliases": []})
        entry_id = entry_res.json()["id"]
        await client.patch(f"/glossary/{entry_id}", json={"spellings": {"es_LATAM": "Kim Hyun"}})

        r = await client.patch(f"/glossary/{entry_id}",
                                json={"spellings": {"es_LATAM": "Kim Hyeon"}})
        assert r.status_code == 200
        assert r.json()["spellings"] == {"es_LATAM": "Kim Hyeon"}


@pytest.mark.asyncio
async def test_delete_glossary_entry_removes_it_from_listing():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        title_res = await client.post("/titles", json={"name": "T", "type": "series"})
        title_id = title_res.json()["id"]
        entry_res = await client.post(f"/titles/{title_id}/glossary",
                                       json={"korean_term": "김현", "category": "person", "aliases": []})
        entry_id = entry_res.json()["id"]

        r = await client.delete(f"/glossary/{entry_id}")
        assert r.status_code == 200

        listed = await client.get("/titles")
    title = next(t for t in listed.json() if t["id"] == title_id)
    assert title["glossary"] == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_api_titles.py::test_list_titles_includes_glossary tests/test_api_titles.py::test_create_glossary_entry_and_patch_spelling tests/test_api_titles.py::test_patch_glossary_entry_overwrites_existing_spelling tests/test_api_titles.py::test_delete_glossary_entry_removes_it_from_listing -v`
Expected: FAIL — `KeyError: 'glossary'`(목록 응답에 필드 없음), `404 Not Found`(엔드포인트 없음)

- [ ] **Step 3: `GET /titles`에 `glossary` 필드 추가**

`backend/app/routers/titles.py` 상단 import에 `GlossaryEntry`, `GlossarySpelling` 추가:

```python
from app.models import (
    Title, Episode, TargetVersion, FindingRow, CharacterGenderFact,
    GlossaryEntry, GlossarySpelling,
)
```

`list_titles` 함수 안, `gender_facts_by_title` 계산 블록 바로 다음에 추가:

```python
        glossary_entry_rows = (await session.execute(
            select(GlossaryEntry).order_by(GlossaryEntry.korean_term)
        )).scalars().all()
        glossary_spelling_rows = (await session.execute(
            select(GlossarySpelling)
        )).scalars().all()
        spellings_by_entry: dict = {}
        for s in glossary_spelling_rows:
            spellings_by_entry.setdefault(s.entry_id, {})[f"{s.language}_{s.variant}"] = s.canonical
        glossary_by_title: dict = {}
        for e in glossary_entry_rows:
            glossary_by_title.setdefault(e.title_id, []).append({
                "id": e.id, "korean_term": e.korean_term, "category": e.category,
                "aliases": e.aliases, "spellings": spellings_by_entry.get(e.id, {}),
            })
```

함수 끝의 `return [...]` 리스트 컴프리헨션에 `"glossary"` 키 추가:

```python
        return [
            {"id": t.id, "name": t.name, "type": t.type,
             "episodes": episodes_by_title.get(t.id, []),
             "character_genders": gender_facts_by_title.get(t.id, []),
             "glossary": glossary_by_title.get(t.id, [])}
            for t in titles
        ]
```

- [ ] **Step 4: CRUD 엔드포인트 구현**

`backend/app/routers/titles.py` 상단 import에 repository 함수 추가:

```python
from app.repositories import create_glossary_entry, update_glossary_entry, delete_glossary_entry
```

`CharacterGenderUpdateIn` 클래스 다음에 Pydantic 모델 추가:

```python
class GlossaryEntryIn(BaseModel):
    korean_term: str
    category: str
    aliases: list[str] = []


class GlossaryEntryUpdateIn(BaseModel):
    category: str | None = None
    aliases: list[str] | None = None
    spellings: dict[str, str] | None = None
```

`update_character_gender` 라우터 함수 다음에 엔드포인트 추가:

```python
@router.post("/titles/{title_id}/glossary")
async def create_glossary_entry_route(title_id: str, payload: GlossaryEntryIn):
    async with async_session() as session:
        title = await session.get(Title, title_id)
        if title is None:
            raise HTTPException(404, "title not found")
        entry = await create_glossary_entry(
            session, title_id, payload.korean_term, payload.category, payload.aliases)
        await session.commit()
        return {"id": entry.id, "korean_term": entry.korean_term, "category": entry.category,
                "aliases": entry.aliases, "spellings": {}}


@router.patch("/glossary/{entry_id}")
async def update_glossary_entry_route(entry_id: str, payload: GlossaryEntryUpdateIn):
    async with async_session() as session:
        entry = await update_glossary_entry(
            session, entry_id, category=payload.category, aliases=payload.aliases,
            spellings=payload.spellings)
        if entry is None:
            raise HTTPException(404, "glossary entry not found")
        await session.commit()
        spellings_rows = (await session.execute(
            select(GlossarySpelling).where(GlossarySpelling.entry_id == entry_id)
        )).scalars().all()
        return {"id": entry.id, "korean_term": entry.korean_term, "category": entry.category,
                "aliases": entry.aliases,
                "spellings": {f"{s.language}_{s.variant}": s.canonical for s in spellings_rows}}


@router.delete("/glossary/{entry_id}")
async def delete_glossary_entry_route(entry_id: str):
    async with async_session() as session:
        deleted = await delete_glossary_entry(session, entry_id)
        if not deleted:
            raise HTTPException(404, "glossary entry not found")
        await session.commit()
        return {"deleted": True}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && ./venv/bin/pytest tests/test_api_titles.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/titles.py backend/tests/test_api_titles.py
git commit -m "feat: add glossary CRUD API endpoints"
```

---

## Task 10: 프론트엔드 — 용어집 관리 UI

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/views/TitleArchiveList.jsx`
- Modify: `frontend/src/views/ReviewView.jsx`

**Interfaces:**
- Consumes: `GET /titles`의 `glossary` 필드, `POST /titles/{title_id}/glossary`, `PATCH /glossary/{entry_id}`, `DELETE /glossary/{entry_id}` (Task 9).
- Produces: `api.js`에 `postGlossaryEntry(titleId, data)`, `patchGlossaryEntry(entryId, data)`, `deleteGlossaryEntry(entryId)` 함수.

- [ ] **Step 1: `api.js`에 함수 추가**

`frontend/src/api.js`의 기존 `request()` 헬퍼와 title 관련 export 함수들 근처에 추가(기존 함수들의 정확한 패턴 — 예: `updateCharacterGender`가 있다면 그 스타일 — 을 그대로 따른다):

```javascript
export function postGlossaryEntry(titleId, data) {
  return request(`/titles/${titleId}/glossary`, { method: "POST", body: data });
}

export function patchGlossaryEntry(entryId, data) {
  return request(`/glossary/${entryId}`, { method: "PATCH", body: data });
}

export function deleteGlossaryEntry(entryId) {
  return request(`/glossary/${entryId}`, { method: "DELETE" });
}
```

(`request()`의 정확한 옵션 이름 — `method`/`body` 등 — 은 파일에 이미 있는 다른 POST/PATCH/DELETE 호출부의 실제 시그니처를 그대로 따른다.)

- [ ] **Step 2: `TitleArchiveList.jsx`에 용어집 피벗 테이블 추가**

`frontend/src/views/TitleArchiveList.jsx`에서 캐릭터-성별 `<details>` 블록(기존 `character_genders.length > 0`을 조건으로 렌더링하는 블록) 바로 다음 형제 요소로 추가. 캐릭터-성별 블록과 달리 **항상 렌더링**한다(비어 있어도 등록 UI를 노출해야 하므로):

```jsx
<details className="mt-2 text-sm">
  <summary className="cursor-pointer text-gray-600">
    작품 용어집 ({title.glossary.length}개)
  </summary>
  <div className="mt-2 overflow-x-auto">
    <table className="w-full border-collapse text-xs">
      <thead>
        <tr>
          <th className="border px-2 py-1 text-left">한국어 용어</th>
          {glossaryLanguageColumns(title).map((col) => (
            <th key={col} className="border px-2 py-1 text-left">{col}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {title.glossary.map((entry) => (
          <tr key={entry.id}>
            <td className="border px-2 py-1">{entry.korean_term}</td>
            {glossaryLanguageColumns(title).map((col) => (
              <td key={col} className="border px-2 py-1">
                <GlossarySpellingCell
                  entry={entry}
                  columnKey={col}
                  onSaved={onGlossaryChanged}
                />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
    <button
      type="button"
      className="mt-1 text-xs text-blue-600"
      onClick={() => onAddGlossaryEntry(title.id)}
    >
      + 용어 추가
    </button>
  </div>
</details>
```

컴포넌트 파일 상단(다른 헬퍼 함수들 근처)에 추가:

```jsx
function glossaryLanguageColumns(title) {
  const keys = new Set();
  title.episodes.forEach((ep) => {
    ep.target_versions.forEach((tv) => {
      keys.add(`${tv.target_language}_${tv.variant}`);
    });
  });
  return Array.from(keys);
}

function GlossarySpellingCell({ entry, columnKey, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(entry.spellings[columnKey] || "");

  if (!editing) {
    return (
      <span className="cursor-pointer" onClick={() => setEditing(true)}>
        {entry.spellings[columnKey] || "—"}
      </span>
    );
  }
  return (
    <input
      className="w-full border px-1"
      autoFocus
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={async () => {
        setEditing(false);
        if (value !== (entry.spellings[columnKey] || "")) {
          await patchGlossaryEntry(entry.id, { spellings: { [columnKey]: value } });
          onSaved();
        }
      }}
    />
  );
}
```

`patchGlossaryEntry`/`postGlossaryEntry` import를 파일 상단 `api.js` import 줄에 추가하고, `useState`가 이미 임포트되어 있는지 확인한다(없으면 `import { useState } from "react"` 추가). `onAddGlossaryEntry`/`onGlossaryChanged`는 이 컴포넌트를 렌더링하는 부모(목록을 새로고침하는 콜백을 이미 갖고 있을 것 — `character_genders` PATCH 후 목록을 새로고침하는 기존 콜백과 동일한 함수를 재사용한다)에서 다음과 같이 정의한다:

```jsx
async function onAddGlossaryEntry(titleId) {
  const koreanTerm = window.prompt("한국어 용어(예: 인물 이름)를 입력하세요");
  if (!koreanTerm) return;
  await postGlossaryEntry(titleId, { korean_term: koreanTerm, category: "person", aliases: [] });
  await refreshTitles();
}

function onGlossaryChanged() {
  refreshTitles();
}
```

(`refreshTitles`는 이 파일에 이미 있는, `GET /titles`를 다시 불러 상태를 갱신하는 기존 함수 이름을 그대로 쓴다 — character_genders PATCH 후 호출하는 것과 동일한 함수다.)

- [ ] **Step 3: `ReviewView.jsx`에 `glossary` 카테고리 라벨/배지 추가**

`frontend/src/views/ReviewView.jsx`의 `CATEGORY_LABELS` 객체에 항목 추가:

```javascript
const CATEGORY_LABELS = {
  sensitivity: "비속어",
  mistranslation: "오역",
  nuance_tone: "뉘앙스",
  unnatural_style: "부자연스러움",
  locale_convention: "현지화",
  glossary: "표기 통일",
};
```

`CATEGORY_BADGE_CLASS` 객체에 기존 6종 팔레트 중 아직 안 쓰인 하나를 재사용해 추가(파일을 열어 실제로 어떤 `bg-finding-*` 클래스가 이미 쓰이고 있는지 확인한 뒤, 그중 가장 근접한 의미의 미사용 색을 고른다 — 예를 들어 `locale_convention`이 `bg-finding-teal`을 쓰고 있다면 `glossary`는 다른 미사용 색인 `bg-finding-indigo`를 선택한다). 예시(실제 파일의 정확한 클래스명 목록에 맞게 조정):

```javascript
const CATEGORY_BADGE_CLASS = {
  sensitivity: "bg-finding-red",
  mistranslation: "bg-finding-orange",
  nuance_tone: "bg-finding-yellow",
  unnatural_style: "bg-finding-purple",
  locale_convention: "bg-finding-teal",
  glossary: "bg-finding-indigo",
};
```

(정확한 기존 6개 클래스명은 파일을 열어 확인 후 그대로 유지하고, `glossary`에는 그 6개 중 나머지 하나를 배정한다 — "새 색상을 만들지 않는다"는 코드 주석 원칙을 지킨다.)

- [ ] **Step 4: 개발 서버에서 수동 확인**

Run: `cd frontend && npm run dev` (백그라운드로 실행)

브라우저에서:
1. 작품 하나를 열어 "작품 용어집 (0개)" `<details>`가 캐릭터-성별 블록 옆에 항상 보이는지 확인.
2. "+ 용어 추가"로 한국어 용어를 등록하고, 언어 컬럼 셀을 클릭해 표기를 입력한 뒤 blur하면 저장되고 화면에 반영되는지 확인.
3. 검수 화면(`ReviewView`)에서 `category: "glossary"`인 finding이 있다면(백엔드에서 실제로 발생시키기 어려우면 브라우저 devtools로 응답을 목업해도 된다) "표기 통일" 배지가 기존 팔레트 색으로 표시되는지 확인.

Expected: 위 세 가지 모두 정상 동작. 회귀 없음(기존 캐릭터-성별 블록/다른 finding 카테고리 배지가 그대로 보임).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.js frontend/src/views/TitleArchiveList.jsx frontend/src/views/ReviewView.jsx
git commit -m "feat: add title glossary management UI"
```

---

## Self-Review

**1. Spec coverage:**
- §데이터 모델 → Task 5 (`glossary_entries`/`glossary_spellings`, unique 제약, 언어별 표기 누락=미등록 의미).
- §처리 흐름 1(주입) → Task 8 (`_run_phase2_and_save`가 분석 시작 시 `get_glossary_prompt_entries` 조회 후 `run_pipeline_phase2`에 전달).
- §처리 흐름 2(추출) → Task 7 (`_extract_glossary_terms_pass`, 정렬된 쌍을 경량 모델에 전달).
- §처리 흐름 3(저장) → Task 6 (`upsert_glossary_extraction`, title 단위 upsert, 기존 표기 미덮어씀) + Task 8(배선).
- §처리 흐름 4(적용) → Task 8 (다음 회차/언어에서 `get_glossary_prompt_entries`가 이미 채워진 결과를 반환).
- §API → Task 9 (`GET /titles` glossary 필드, `POST /titles/{title_id}/glossary`, `PATCH /glossary/{entry_id}`, `DELETE /glossary/{entry_id}`).
- §UI → Task 10 (피벗 테이블, 항상 렌더링되는 `<details>`, 셀 클릭 편집, "+ 용어 추가", `ReviewView` 라벨/배지).
- §프롬프트 변경 3(체크리스트 항목) → Task 1 (`build_verification_checklist`의 glossary_item).
- §프롬프트 변경 4(주입 지점) → Task 2 (`system += glossary_block` — 참고 지식베이스 줄 바로 다음).
- §프롬프트 변경 5(카테고리 enum/스키마) → Task 1 (`CATEGORY_ENUM`, `build_findings_schema_instruction`; `schemas.py`의 `FindingCategory`는 이미 `"glossary"` 포함되어 있어 변경 불필요 — 확인 완료).
- §범위 제외(글로서리 삭제, 소급 미적용, 크로스 title 미공유, ReviewView finding 카드에 용어집 미노출) → Task 4(삭제), Global Constraints(소급/크로스title), Task 10(카드는 배지만 추가, 용어집 표시 없음).
- 갭 없음 — 모든 대상 섹션에 대응하는 태스크가 있다.

**2. Placeholder scan:** 모든 스텝에 실제 코드/실제 테스트 어서션이 포함되어 있다. "TODO"/"적절히 처리" 류 표현 없음. 단, Task 4 Step 1과 Task 9 Step 4의 일부 코드 블록은 "기존 파일의 정확한 값과 다르면 그대로 유지"라는 조건부 지시를 명시적으로 달아두었다 — 이는 플레이스홀더가 아니라, 이 세션에서 실제로 원본 파일 전체를 읽어 확인하지 못한 두 지점(파일 전체를 이미 재확인한 base.py/claude_client.py/gpt_client.py/models.py/repositories.py/routers/titles.py/pretreatment.py와 달리, `test_pretreatment.py`의 상수 값과 `background.py`의 `...` 생략부)에 대한 정직한 실행 지침이다.

**3. Type consistency:** `glossary_entries: Optional[List[dict]] = None`이 `base.py`(ABC) → `claude_client.py`/`gpt_client.py` → `live.py`/`mock.py` → `pipeline.py`(`_run_dual_verification_pass`/`run_pipeline_phase2`) → `background.py`까지 동일한 이름/타입/위치(마지막, 기본값 `None`)로 일관되게 쓰였다. `build_glossary_block(entries: List[dict]) -> str`과 `get_glossary_prompt_entries`의 반환 dict 키(`korean_term`/`category`/`aliases`/`canonical`)가 정확히 일치한다. `upsert_glossary_extraction`이 받는 extraction dict 키(`korean_term`/`category`/`canonical`)는 `extract_glossary_terms`의 반환 형태와 일치한다. `spellings` 딕셔너리 키 형식(`"{language}_{variant}"`)이 `repositories.py`(`_language_variant_key`), `routers/titles.py`(`list_titles`/PATCH 응답), `TitleArchiveList.jsx`(`glossaryLanguageColumns`) 세 곳 모두 동일하다.

---

Plan complete and saved to `docs/superpowers/plans/2026-09-07-glossary-term-consistency.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
